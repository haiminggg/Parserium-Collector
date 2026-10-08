from datetime import datetime
from typing import Protocol
from uuid import UUID

from parserium_collector.features.activity.errors import (
    ActivityConflictError,
    ActivityNotFoundError,
)
from parserium_collector.features.activity.models import (
    ActivityJobRecord,
    ActivityJobResponse,
    ActivityJobState,
    ActivityJobType,
    ActivityPage,
    ActivityPageResponse,
    ActivitySummaryResponse,
)
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope


class ActivityRepository(Protocol):
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
    ) -> ActivityPage: ...

    async def get_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
    ) -> ActivityJobRecord | None: ...

    async def soft_delete_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
        now: datetime,
    ) -> bool: ...


class ActivityService:
    def __init__(self, repository: ActivityRepository) -> None:
        self._repository = repository

    async def list_activity(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
        cursor: str | None,
        job_type: ActivityJobType | None = None,
        state: ActivityJobState | None = None,
        creator_id: UUID | None = None,
        created_after: datetime | None = None,
    ) -> ActivityPageResponse:
        page = await self._repository.list_activity(
            scope.workspace_id,
            limit=limit,
            cursor=cursor,
            job_type=job_type,
            state=state,
            creator_id=creator_id,
            created_after=created_after,
        )
        return ActivityPageResponse(
            items=[self._response(scope, item) for item in page.items],
            total=page.total,
            next_cursor=page.next_cursor,
            summary=ActivitySummaryResponse(**page.summary.__dict__),
        )

    async def delete_history(
        self,
        scope: WorkspaceScope,
        job_type: ActivityJobType,
        job_id: UUID,
        now: datetime,
    ) -> None:
        item = await self._repository.get_activity(scope.workspace_id, job_type, job_id)
        if item is None:
            return
        if not self._authorized(scope, item):
            raise ActivityNotFoundError("The activity item does not exist.")
        if item.state in {ActivityJobState.QUEUED, ActivityJobState.ACTIVE}:
            raise ActivityConflictError("Active activity history cannot be deleted.")
        await self._repository.soft_delete_activity(
            scope.workspace_id,
            job_type,
            job_id,
            now,
        )

    @classmethod
    def _response(
        cls,
        scope: WorkspaceScope,
        item: ActivityJobRecord,
    ) -> ActivityJobResponse:
        authorized = cls._authorized(scope, item)
        terminal = item.state not in {ActivityJobState.QUEUED, ActivityJobState.ACTIVE}
        response_data = item.__dict__.copy()
        response_data.pop("retryable")
        return ActivityJobResponse(
            **response_data,
            can_cancel=(authorized and item.job_type is ActivityJobType.DISCOVERY and not terminal),
            can_retry=(
                authorized
                and (
                    item.job_type is ActivityJobType.DISCOVERY
                    and item.state in {ActivityJobState.FAILED, ActivityJobState.CANCELLED}
                    or item.job_type is ActivityJobType.COLLECTION
                    and item.state is ActivityJobState.FAILED
                    and item.retryable is True
                )
            ),
            can_delete=authorized and terminal,
        )

    @staticmethod
    def _authorized(scope: WorkspaceScope, item: ActivityJobRecord) -> bool:
        return scope.role is WorkspaceRole.OWNER or (
            scope.user_id is not None and scope.user_id == item.created_by_user_id
        )
