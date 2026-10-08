from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from parserium_collector.features.activity.models import (
    ActivityJobResponse,
    ActivityJobState,
    ActivityJobType,
    ActivityPageResponse,
    ActivitySummaryResponse,
)

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_activity_page_exposes_one_safe_shape_for_every_job_type() -> None:
    job = ActivityJobResponse(
        id=uuid4(),
        job_type=ActivityJobType.DISCOVERY,
        title="quarterly investment reports",
        subtitle="Primary Firecrawl",
        state=ActivityJobState.ACTIVE,
        stage="discovering",
        progress_percent=None,
        created_by_user_id=uuid4(),
        created_by_name="Owner",
        related_document_id=None,
        error_code=None,
        can_cancel=True,
        can_retry=False,
        can_delete=False,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )

    page = ActivityPageResponse(
        items=[job],
        total=1,
        next_cursor=None,
        summary=ActivitySummaryResponse(
            active=1,
            queued=0,
            failed=0,
            completed=0,
        ),
    )

    assert page.items[0].job_type == "discovery"
    assert page.items[0].can_cancel is True
    assert "claimed_by" not in page.model_dump_json()


def test_activity_response_rejects_invalid_progress_and_unknown_fields() -> None:
    values = {
        "id": uuid4(),
        "job_type": "collection",
        "title": "Annual report.pdf",
        "subtitle": None,
        "state": "queued",
        "stage": "queued",
        "progress_percent": 101,
        "created_by_user_id": None,
        "created_by_name": None,
        "related_document_id": None,
        "error_code": None,
        "can_cancel": False,
        "can_retry": False,
        "can_delete": False,
        "created_at": NOW,
        "updated_at": NOW,
        "completed_at": None,
    }
    with pytest.raises(ValidationError):
        ActivityJobResponse.model_validate(values)
    with pytest.raises(ValidationError):
        ActivityJobResponse.model_validate(
            {**values, "progress_percent": 50, "worker_secret": "hidden"}
        )
