from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class PageCursor:
    created_at: datetime
    id: UUID


@dataclass(frozen=True)
class Page[RecordT]:
    items: tuple[RecordT, ...]
    total: int
    next_cursor: PageCursor | None


def encode_page_cursor(cursor: PageCursor) -> str:
    payload = f"{cursor.created_at.isoformat()}|{cursor.id}".encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def decode_page_cursor(value: str) -> PageCursor:
    try:
        if not value:
            raise ValueError
        padded = value + "=" * (-len(value) % 4)
        decoded = b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        timestamp, identifier = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
        return PageCursor(created_at=created_at, id=UUID(identifier))
    except (Base64Error, UnicodeError, ValueError) as error:
        raise ValueError("The pagination cursor is invalid.") from error
