from datetime import UTC, datetime
from uuid import UUID

import pytest

from parserium_collector.features.acquisition.pagination import (
    PageCursor,
    decode_page_cursor,
    encode_page_cursor,
)


def test_page_cursor_round_trips_as_an_opaque_url_safe_value() -> None:
    cursor = PageCursor(
        created_at=datetime(2026, 8, 25, 12, 30, 45, 123456, tzinfo=UTC),
        id=UUID("00000000-0000-4000-8000-000000000123"),
    )

    encoded = encode_page_cursor(cursor)

    assert "+" not in encoded
    assert "/" not in encoded
    assert decode_page_cursor(encoded) == cursor


@pytest.mark.parametrize(
    "value",
    (
        "",
        "not-base64!",
        "bm90LWFuLWludmFsaWQtY3Vyc29y",
    ),
)
def test_invalid_page_cursors_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="cursor"):
        decode_page_cursor(value)
