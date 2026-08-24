from sqlalchemy import Column, DateTime, MetaData, String, Table

metadata = MetaData()

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)
