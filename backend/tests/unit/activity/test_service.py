from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from parserium_collector.features.activity.errors import (
    ActivityConflictError,
    ActivityNotFoundError,
)
from parserium_collector.features.activity.models import (
    ActivityJobRecord,
    ActivityJobState,
    ActivityJobType,
    ActivityPage,
    ActivitySummary,
)
from parserium_collector.features.activity.service import ActivityService
from parserium_collector.features.identity.models import WorkspaceRole, WorkspaceScope

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
WORKSPACE_ID = uuid4()
CREATOR_ID = uuid4()


def record(
    *,
    state: ActivityJobState = ActivityJobState.COMPLETED,
    job_type: ActivityJobType = ActivityJobType.DISCOVERY,
    retryable: bool | None = None,
) -> ActivityJobRecord:
    return ActivityJobRecord(
        id=uuid4(),
        job_type=job_type,
        title="quarterly reports",
        subtitle=None,
        state=state,
        stage=state.value,
        progress_percent=100 if state is ActivityJobState.COMPLETED else None,
        created_by_user_id=CREATOR_ID,
        created_by_name="Creator",
        related_document_id=None,
        error_code=None,
        retryable=retryable,
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW if state is ActivityJobState.COMPLETED else None,
    )


class RepositoryDouble:
    def __init__(self, item: ActivityJobRecord) -> None:
        self.item = item
        self.deleted: tuple[UUID, ActivityJobType, UUID, datetime] | None = None

    async def list_activity(self, workspace_id: UUID, **_: object) -> ActivityPage:
        assert workspace_id == WORKSPACE_ID
        return ActivityPage(
            items=(self.item,),
            total=1,
            next_cursor=None,
            summary=ActivitySummary(active=0, queued=0, failed=0, completed=1),
        )

    async def get_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
    ) -> ActivityJobRecord | None:
        if workspace_id != WORKSPACE_ID or job_type is not self.item.job_type:
            return None
        return self.item if job_id == self.item.id else None

    async def soft_delete_activity(
        self,
        workspace_id: UUID,
        job_type: ActivityJobType,
        job_id: UUID,
        now: datetime,
    ) -> bool:
        self.deleted = (workspace_id, job_type, job_id, now)
        return True


async def test_creator_can_list_and_delete_terminal_history() -> None:
    repository = RepositoryDouble(record())
    service = ActivityService(repository)
    scope = WorkspaceScope(WORKSPACE_ID, CREATOR_ID, WorkspaceRole.MEMBER)

    page = await service.list_activity(scope, limit=50, cursor=None)
    await service.delete_history(scope, repository.item.job_type, repository.item.id, NOW)

    assert page.items[0].can_delete is True
    assert page.items[0].can_cancel is False
    assert repository.deleted == (
        WORKSPACE_ID,
        ActivityJobType.DISCOVERY,
        repository.item.id,
        NOW,
    )


async def test_member_cannot_delete_another_users_history() -> None:
    repository = RepositoryDouble(record())
    service = ActivityService(repository)
    scope = WorkspaceScope(WORKSPACE_ID, uuid4(), WorkspaceRole.MEMBER)

    with pytest.raises(ActivityNotFoundError):
        await service.delete_history(scope, repository.item.job_type, repository.item.id, NOW)


async def test_active_history_cannot_be_deleted() -> None:
    repository = RepositoryDouble(record(state=ActivityJobState.ACTIVE))
    service = ActivityService(repository)
    scope = WorkspaceScope(WORKSPACE_ID, CREATOR_ID, WorkspaceRole.MEMBER)

    with pytest.raises(ActivityConflictError):
        await service.delete_history(scope, repository.item.job_type, repository.item.id, NOW)


async def test_non_retryable_collection_failure_does_not_offer_retry() -> None:
    repository = RepositoryDouble(
        record(
            state=ActivityJobState.FAILED,
            job_type=ActivityJobType.COLLECTION,
            retryable=False,
        )
    )
    service = ActivityService(repository)
    scope = WorkspaceScope(WORKSPACE_ID, CREATOR_ID, WorkspaceRole.MEMBER)

    page = await service.list_activity(scope, limit=50, cursor=None)

    assert page.items[0].can_retry is False
