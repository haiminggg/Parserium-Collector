from datetime import datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from parserium_collector.adapters.database.tables import local_sessions, pairing_codes
from parserium_collector.features.session.models import SessionRecord


class SessionRepository(Protocol):
    async def replace_pairing_code(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def exchange_pairing_code(
        self,
        code_digest: str,
        token_digest: str,
        now: datetime,
    ) -> bool: ...

    async def load_session(self, token_digest: str) -> SessionRecord | None: ...

    async def touch_session(self, token_digest: str, now: datetime) -> None: ...

    async def revoke_session(self, token_digest: str, now: datetime) -> None: ...

    async def has_active_session(self, active_after: datetime) -> bool: ...

    async def has_active_pairing(self, now: datetime) -> bool: ...

    async def revoke_all_and_replace_pairing(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None: ...


class PostgresSessionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def replace_pairing_code(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(delete(pairing_codes))
            await connection.execute(
                insert(pairing_codes).values(
                    id=uuid4(),
                    code_digest=code_digest,
                    created_at=created_at,
                    expires_at=expires_at,
                    used_at=None,
                )
            )

    async def exchange_pairing_code(
        self,
        code_digest: str,
        token_digest: str,
        now: datetime,
    ) -> bool:
        consume = (
            update(pairing_codes)
            .where(
                pairing_codes.c.code_digest == code_digest,
                pairing_codes.c.used_at.is_(None),
                pairing_codes.c.expires_at > now,
            )
            .values(used_at=now)
            .returning(pairing_codes.c.id)
        )
        async with self._engine.begin() as connection:
            consumed = (await connection.execute(consume)).scalar_one_or_none()
            if consumed is None:
                return False
            await connection.execute(
                insert(local_sessions).values(
                    token_digest=token_digest,
                    created_at=now,
                    last_seen_at=now,
                    revoked_at=None,
                )
            )
        return True

    async def load_session(self, token_digest: str) -> SessionRecord | None:
        statement = select(
            local_sessions.c.token_digest,
            local_sessions.c.created_at,
            local_sessions.c.last_seen_at,
            local_sessions.c.revoked_at,
        ).where(local_sessions.c.token_digest == token_digest)
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return SessionRecord(
            token_digest=row.token_digest,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
        )

    async def touch_session(self, token_digest: str, now: datetime) -> None:
        statement = (
            update(local_sessions)
            .where(
                local_sessions.c.token_digest == token_digest,
                local_sessions.c.revoked_at.is_(None),
            )
            .values(last_seen_at=now)
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def revoke_session(self, token_digest: str, now: datetime) -> None:
        statement = (
            update(local_sessions)
            .where(local_sessions.c.token_digest == token_digest)
            .values(revoked_at=now)
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def has_active_session(self, active_after: datetime) -> bool:
        statement = (
            select(local_sessions.c.token_digest)
            .where(
                local_sessions.c.revoked_at.is_(None),
                local_sessions.c.last_seen_at > active_after,
            )
            .limit(1)
        )
        async with self._engine.connect() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def has_active_pairing(self, now: datetime) -> bool:
        statement = (
            select(pairing_codes.c.id)
            .where(
                pairing_codes.c.used_at.is_(None),
                pairing_codes.c.expires_at > now,
            )
            .limit(1)
        )
        async with self._engine.connect() as connection:
            return (await connection.execute(statement)).scalar_one_or_none() is not None

    async def revoke_all_and_replace_pairing(
        self,
        code_digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                update(local_sessions)
                .where(local_sessions.c.revoked_at.is_(None))
                .values(revoked_at=created_at)
            )
            await connection.execute(delete(pairing_codes))
            await connection.execute(
                insert(pairing_codes).values(
                    id=uuid4(),
                    code_digest=code_digest,
                    created_at=created_at,
                    expires_at=expires_at,
                    used_at=None,
                )
            )
