from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Integer,
    Text,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from parserium_collector.adapters.database.tables import (
    candidate_analyses,
    collection_jobs,
    discovery_analysis_sessions,
    document_exports,
    users,
)
from parserium_collector.features.activity.models import (
    ActivityJobRecord,
    ActivityJobState,
    ActivityJobType,
    ActivityPage,
    ActivitySummary,
)


@dataclass(frozen=True)
class ActivityCursor:
    created_at: datetime
    job_type: ActivityJobType
    id: UUID


def encode_activity_cursor(cursor: ActivityCursor) -> str:
    payload = f"{cursor.created_at.isoformat()}|{cursor.job_type.value}|{cursor.id}".encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def decode_activity_cursor(value: str) -> ActivityCursor:
    try:
        if not value or len(value) > 512:
            raise ValueError
        padded = value + "=" * (-len(value) % 4)
        decoded = b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        timestamp, raw_type, identifier = decoded.split("|", maxsplit=2)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
        return ActivityCursor(
            created_at=created_at,
            job_type=ActivityJobType(raw_type),
            id=UUID(identifier),
        )
    except (Base64Error, UnicodeError, ValueError) as error:
        raise ValueError("The activity pagination cursor is invalid.") from error


def _state(
    status: ColumnElement[Any],
    mapping: dict[str, ActivityJobState],
) -> ColumnElement[Any]:
    return case(
        *[(status == source, target.value) for source, target in mapping.items()],
        else_=ActivityJobState.FAILED.value,
    )


def _progress(
    numerator: ColumnElement[Any],
    denominator: ColumnElement[Any],
) -> ColumnElement[Any]:
    return case(
        (denominator > 0, func.least(100, cast(numerator * 100 / denominator, Integer))),
        else_=None,
    )


class PostgresActivityRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @staticmethod
    def _activity_query(workspace_id: UUID) -> Subquery:
        discovery_user = users.alias("discovery_user")
        collection_user = users.alias("collection_user")
        analysis_user = users.alias("analysis_user")
        export_user = users.alias("export_user")
        analysis_sessions = discovery_analysis_sessions.alias("analysis_sessions")

        discovery = (
            select(
                discovery_analysis_sessions.c.id.label("id"),
                literal(ActivityJobType.DISCOVERY.value).label("job_type"),
                cast(discovery_analysis_sessions.c.query, Text).label("title"),
                cast(discovery_analysis_sessions.c.firecrawl_connection_name_snapshot, Text).label(
                    "subtitle"
                ),
                _state(
                    discovery_analysis_sessions.c.status,
                    {
                        "queued": ActivityJobState.QUEUED,
                        "running": ActivityJobState.ACTIVE,
                        "completed": ActivityJobState.COMPLETED,
                        "failed": ActivityJobState.FAILED,
                        "cancelled": ActivityJobState.CANCELLED,
                    },
                ).label("state"),
                discovery_analysis_sessions.c.job_stage.label("stage"),
                case(
                    (discovery_analysis_sessions.c.status == "completed", 100),
                    else_=None,
                ).label("progress_percent"),
                discovery_analysis_sessions.c.created_by_user_id.label("created_by_user_id"),
                func.coalesce(discovery_user.c.display_name, discovery_user.c.email).label(
                    "created_by_name"
                ),
                cast(literal(None), discovery_analysis_sessions.c.id.type).label(
                    "related_document_id"
                ),
                discovery_analysis_sessions.c.error_code.label("error_code"),
                literal(True).label("retryable"),
                discovery_analysis_sessions.c.created_at.label("created_at"),
                discovery_analysis_sessions.c.updated_at.label("updated_at"),
                discovery_analysis_sessions.c.completed_at.label("completed_at"),
            )
            .select_from(
                discovery_analysis_sessions.outerjoin(
                    discovery_user,
                    discovery_user.c.id == discovery_analysis_sessions.c.created_by_user_id,
                )
            )
            .where(
                discovery_analysis_sessions.c.workspace_id == workspace_id,
                discovery_analysis_sessions.c.history_deleted_at.is_(None),
            )
        )
        collection = (
            select(
                collection_jobs.c.id,
                literal(ActivityJobType.COLLECTION.value),
                cast(func.coalesce(collection_jobs.c.title, collection_jobs.c.source_url), Text),
                cast(collection_jobs.c.source_url, Text),
                _state(
                    collection_jobs.c.status,
                    {
                        "queued": ActivityJobState.QUEUED,
                        "downloading": ActivityJobState.ACTIVE,
                        "validating": ActivityJobState.ACTIVE,
                        "completed": ActivityJobState.COMPLETED,
                        "duplicate": ActivityJobState.COMPLETED,
                        "failed": ActivityJobState.FAILED,
                    },
                ),
                collection_jobs.c.status,
                case(
                    (collection_jobs.c.status.in_(("completed", "duplicate")), 100),
                    else_=_progress(
                        collection_jobs.c.bytes_downloaded,
                        collection_jobs.c.content_length,
                    ),
                ),
                collection_jobs.c.created_by_user_id,
                func.coalesce(collection_user.c.display_name, collection_user.c.email),
                collection_jobs.c.document_id,
                collection_jobs.c.error_code,
                collection_jobs.c.error_retryable,
                collection_jobs.c.created_at,
                collection_jobs.c.updated_at,
                collection_jobs.c.completed_at,
            )
            .select_from(
                collection_jobs.outerjoin(
                    collection_user,
                    collection_user.c.id == collection_jobs.c.created_by_user_id,
                )
            )
            .where(
                collection_jobs.c.workspace_id == workspace_id,
                collection_jobs.c.history_deleted_at.is_(None),
            )
        )
        analysis = (
            select(
                candidate_analyses.c.id,
                literal(ActivityJobType.ANALYSIS.value),
                cast(
                    func.coalesce(candidate_analyses.c.title, candidate_analyses.c.source_url),
                    Text,
                ),
                cast(candidate_analyses.c.source_url, Text),
                _state(
                    candidate_analyses.c.status,
                    {
                        "queued": ActivityJobState.QUEUED,
                        "downloading": ActivityJobState.ACTIVE,
                        "validating": ActivityJobState.ACTIVE,
                        "converting": ActivityJobState.ACTIVE,
                        "parsing": ActivityJobState.ACTIVE,
                        "ready": ActivityJobState.COMPLETED,
                        "no_tables": ActivityJobState.COMPLETED,
                        "partial": ActivityJobState.COMPLETED,
                        "promoted": ActivityJobState.COMPLETED,
                        "failed": ActivityJobState.FAILED,
                        "cancelled": ActivityJobState.CANCELLED,
                    },
                ),
                candidate_analyses.c.status,
                case(
                    (
                        candidate_analyses.c.status.in_(
                            ("ready", "no_tables", "partial", "promoted")
                        ),
                        100,
                    ),
                    else_=_progress(
                        candidate_analyses.c.analyzed_page_count,
                        candidate_analyses.c.page_count,
                    ),
                ),
                analysis_sessions.c.created_by_user_id,
                func.coalesce(analysis_user.c.display_name, analysis_user.c.email),
                candidate_analyses.c.promoted_document_id,
                candidate_analyses.c.error_code,
                candidate_analyses.c.error_retryable,
                candidate_analyses.c.created_at,
                candidate_analyses.c.updated_at,
                candidate_analyses.c.completed_at,
            )
            .select_from(
                candidate_analyses.join(
                    analysis_sessions,
                    and_(
                        analysis_sessions.c.id == candidate_analyses.c.session_id,
                        analysis_sessions.c.workspace_id == candidate_analyses.c.workspace_id,
                    ),
                ).outerjoin(
                    analysis_user,
                    analysis_user.c.id == analysis_sessions.c.created_by_user_id,
                )
            )
            .where(
                candidate_analyses.c.workspace_id == workspace_id,
                candidate_analyses.c.history_deleted_at.is_(None),
                analysis_sessions.c.history_deleted_at.is_(None),
            )
        )
        export = (
            select(
                document_exports.c.id,
                literal(ActivityJobType.EXPORT.value),
                cast(document_exports.c.target_filename, Text),
                cast(document_exports.c.relative_directory, Text),
                _state(
                    document_exports.c.status,
                    {
                        "queued": ActivityJobState.QUEUED,
                        "exporting": ActivityJobState.ACTIVE,
                        "completed": ActivityJobState.COMPLETED,
                        "failed": ActivityJobState.FAILED,
                    },
                ),
                document_exports.c.status,
                case((document_exports.c.status == "completed", 100), else_=None),
                document_exports.c.created_by_user_id,
                func.coalesce(export_user.c.display_name, export_user.c.email),
                document_exports.c.document_id,
                document_exports.c.error_code,
                literal(False),
                document_exports.c.created_at,
                document_exports.c.updated_at,
                document_exports.c.completed_at,
            )
            .select_from(
                document_exports.outerjoin(
                    export_user,
                    export_user.c.id == document_exports.c.created_by_user_id,
                )
            )
            .where(
                document_exports.c.workspace_id == workspace_id,
                document_exports.c.history_deleted_at.is_(None),
            )
        )
        return union_all(discovery, collection, analysis, export).subquery("activity")

    async def list_activity(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: str | None,
        job_type: ActivityJobType | None = None,
        state: ActivityJobState | None = None,
        creator_id: UUID | None = None,
        created_after: datetime | None = None,
    ) -> ActivityPage:
        if not 1 <= limit <= 100:
            raise ValueError("The activity page limit must be between 1 and 100.")
        activity = self._activity_query(workspace_id)
        statement = select(activity)
        filters: list[ColumnElement[bool]] = []
        if job_type is not None:
            filters.append(activity.c.job_type == job_type.value)
        if state is not None:
            filters.append(activity.c.state == state.value)
        if creator_id is not None:
            filters.append(activity.c.created_by_user_id == creator_id)
        if created_after is not None:
            filters.append(activity.c.created_at >= created_after)
        if filters:
            statement = statement.where(*filters)
        decoded = decode_activity_cursor(cursor) if cursor is not None else None
        if decoded is not None:
            statement = statement.where(
                or_(
                    activity.c.created_at < decoded.created_at,
                    and_(
                        activity.c.created_at == decoded.created_at,
                        activity.c.job_type < decoded.job_type.value,
                    ),
                    and_(
                        activity.c.created_at == decoded.created_at,
                        activity.c.job_type == decoded.job_type.value,
                        activity.c.id < decoded.id,
                    ),
                )
            )
        statement = statement.order_by(
            activity.c.created_at.desc(),
            activity.c.job_type.desc(),
            activity.c.id.desc(),
        ).limit(limit + 1)
        async with self._engine.connect() as connection:
            summary_row = (
                await connection.execute(
                    select(
                        func.count().label("total"),
                        func.count().filter(activity.c.state == "active").label("active"),
                        func.count().filter(activity.c.state == "queued").label("queued"),
                        func.count().filter(activity.c.state == "failed").label("failed"),
                        func.count().filter(activity.c.state == "completed").label("completed"),
                    ).select_from(activity)
                )
            ).one()
            total = await connection.scalar(
                select(func.count()).select_from(activity).where(*filters)
            )
            rows = (await connection.execute(statement)).mappings().all()
        page_rows = rows[:limit]
        records = tuple(self._record(row) for row in page_rows)
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = encode_activity_cursor(
                ActivityCursor(last.created_at, last.job_type, last.id)
            )
        return ActivityPage(
            items=records,
            total=int(total or 0),
            next_cursor=next_cursor,
            summary=ActivitySummary(
                active=int(summary_row.active),
                queued=int(summary_row.queued),
                failed=int(summary_row.failed),
                completed=int(summary_row.completed),
            ),
        )

    async def get_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
    ) -> ActivityJobRecord | None:
        activity = self._activity_query(workspace_id)
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(activity).where(
                            activity.c.job_type == job_type.value,
                            activity.c.id == job_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else self._record(row)

    async def soft_delete_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
        now: datetime,
    ) -> bool:
        table, terminal_statuses = {
            ActivityJobType.DISCOVERY: (
                discovery_analysis_sessions,
                ("completed", "failed", "cancelled"),
            ),
            ActivityJobType.COLLECTION: (
                collection_jobs,
                ("completed", "duplicate", "failed"),
            ),
            ActivityJobType.ANALYSIS: (
                candidate_analyses,
                ("ready", "no_tables", "partial", "failed", "cancelled", "promoted"),
            ),
            ActivityJobType.EXPORT: (document_exports, ("completed", "failed")),
        }[job_type]
        async with self._engine.begin() as connection:
            deleted = await connection.scalar(
                update(table)
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.id == job_id,
                    table.c.history_deleted_at.is_(None),
                    table.c.status.in_(terminal_statuses),
                )
                .values(history_deleted_at=now)
                .returning(table.c.id)
            )
            if deleted is None:
                return False
            if job_type is ActivityJobType.DISCOVERY:
                await connection.execute(
                    update(candidate_analyses)
                    .where(
                        candidate_analyses.c.workspace_id == workspace_id,
                        candidate_analyses.c.session_id == job_id,
                        candidate_analyses.c.history_deleted_at.is_(None),
                    )
                    .values(history_deleted_at=now)
                )
            return True

    @staticmethod
    def _record(row: RowMapping) -> ActivityJobRecord:
        return ActivityJobRecord(
            id=row["id"],
            job_type=ActivityJobType(row["job_type"]),
            title=row["title"],
            subtitle=row["subtitle"],
            state=ActivityJobState(row["state"]),
            stage=row["stage"],
            progress_percent=row["progress_percent"],
            created_by_user_id=row["created_by_user_id"],
            created_by_name=row["created_by_name"],
            related_document_id=row["related_document_id"],
            error_code=row["error_code"],
            retryable=row["retryable"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )
