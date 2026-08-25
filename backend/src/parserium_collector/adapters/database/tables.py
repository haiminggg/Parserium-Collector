from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid

metadata = MetaData()

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)

pairing_codes = Table(
    "pairing_codes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("code_digest", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("used_at", DateTime(timezone=True)),
)

local_sessions = Table(
    "local_sessions",
    metadata,
    Column("token_digest", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, index=True),
    Column("revoked_at", DateTime(timezone=True)),
)
