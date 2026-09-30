from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parserium_collector.features.activity.models import ActivityJobType
from parserium_collector.features.activity.repository import (
    ActivityCursor,
    decode_activity_cursor,
    encode_activity_cursor,
)


def test_activity_cursor_round_trips_job_type_timestamp_and_id() -> None:
    cursor = ActivityCursor(
        created_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        job_type=ActivityJobType.ANALYSIS,
        id=uuid4(),
    )

    assert decode_activity_cursor(encode_activity_cursor(cursor)) == cursor


@pytest.mark.parametrize("value", ("", "not-base64", "YWJj", "eA" * 300))
def test_activity_cursor_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="pagination cursor"):
        decode_activity_cursor(value)
