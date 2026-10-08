import hashlib
import os
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import anyio
import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client
from sqlalchemy import URL, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from parserium_collector.adapters.database.tables import (
    artifact_objects,
    candidate_analyses,
    discovery_analysis_sessions,
    documents,
    workspaces,
)
from parserium_collector.features.storage.access import (
    ArtifactAccessService,
    SignedArtifactDownload,
)
from parserium_collector.features.storage.errors import (
    ArtifactNotFoundError,
)
from parserium_collector.features.storage.keys import artifact_key
from parserium_collector.features.storage.models import (
    ArtifactKind,
    ArtifactLifecycle,
    ArtifactObjectState,
    ArtifactResourceKind,
    StoredObjectMetadata,
)
from parserium_collector.features.storage.repository import (
    PostgresArtifactRepository,
)
from parserium_collector.features.storage.s3 import S3ArtifactStore

RUN_ID_PATTERN = re.compile(r"[a-f0-9]{12,32}")


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required recovery setting {name} is missing.")
    return value


def read_secret(name: str) -> str:
    path = Path(required_environment(name))
    if not path.is_file():
        raise RuntimeError(f"Recovery secret file for {name} is missing.")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"Recovery secret file for {name} is empty.")
    return value


@dataclass(frozen=True)
class WorkspaceFixture:
    workspace_id: UUID
    document_id: UUID
    analysis_session_id: UUID
    candidate_id: UUID
    document_bytes: bytes
    preview_bytes: bytes

    @property
    def document_key(self) -> str:
        return artifact_key(
            self.workspace_id,
            ArtifactResourceKind.DOCUMENT,
            self.document_id,
            ArtifactKind.STORED_DOCUMENT,
        )

    @property
    def preview_key(self) -> str:
        return artifact_key(
            self.workspace_id,
            ArtifactResourceKind.ANALYSIS,
            self.candidate_id,
            ArtifactKind.PREVIEW_PNG,
        )

    @property
    def expected_bytes(self) -> int:
        return len(self.document_bytes) + len(self.preview_bytes)


@dataclass(frozen=True)
class RecoveryScenario:
    run_id: str
    workspaces: tuple[WorkspaceFixture, WorkspaceFixture]

    @classmethod
    def from_environment(cls) -> "RecoveryScenario":
        run_id = required_environment("TEST_RECOVERY_RUN_ID")
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise RuntimeError("The recovery run identifier is invalid.")

        def identifier(label: str) -> UUID:
            return uuid5(NAMESPACE_URL, f"parserium-recovery:{run_id}:{label}")

        fixtures: list[WorkspaceFixture] = []
        for label in ("a", "b"):
            fixtures.append(
                WorkspaceFixture(
                    workspace_id=identifier(f"workspace-{label}"),
                    document_id=identifier(f"document-{label}"),
                    analysis_session_id=identifier(f"analysis-session-{label}"),
                    candidate_id=identifier(f"candidate-{label}"),
                    document_bytes=(
                        f"%PDF-1.7\nParserium recovery {run_id} workspace {label}\n%%EOF\n"
                    ).encode(),
                    preview_bytes=(b"\x89PNG\r\n\x1a\n" + f"recovery-{run_id}-{label}".encode()),
                )
            )
        return cls(run_id=run_id, workspaces=(fixtures[0], fixtures[1]))


def database_url() -> str:
    return URL.create(
        "postgresql+psycopg",
        username="parserium_collector",
        password=read_secret("TEST_DATABASE_PASSWORD_FILE"),
        host=required_environment("TEST_DATABASE_HOST"),
        port=5432,
        database=required_environment("TEST_DATABASE_NAME"),
    ).render_as_string(hide_password=False)


def create_s3() -> tuple[S3Client, S3ArtifactStore, str, str, Path]:
    endpoint = required_environment("TEST_S3_ENDPOINT")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Recovery requires a TLS S3 endpoint.")
    region = required_environment("TEST_S3_REGION")
    primary_bucket = required_environment("TEST_S3_BUCKET")
    backup_bucket = required_environment("TEST_S3_BACKUP_BUCKET")
    if primary_bucket == backup_bucket:
        raise RuntimeError("Recovery primary and backup buckets must be distinct.")
    ca_bundle = Path(required_environment("TEST_S3_CA_BUNDLE_FILE"))
    if not ca_bundle.is_file():
        raise RuntimeError("Recovery CA bundle is missing.")
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=read_secret("TEST_S3_ACCESS_KEY_FILE"),
        aws_secret_access_key=read_secret("TEST_S3_SECRET_KEY_FILE"),
        verify=str(ca_bundle),
        config=Config(
            signature_version="s3v4",
            connect_timeout=2,
            read_timeout=10,
            retries={"total_max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )
    return (
        client,
        S3ArtifactStore(client=client, bucket=primary_bucket, max_concurrency=2),
        primary_bucket,
        backup_bucket,
        ca_bundle,
    )


async def seed_relational_records(engine: AsyncEngine, scenario: RecoveryScenario) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        existing = (
            await connection.execute(
                select(workspaces.c.id).where(
                    workspaces.c.id.in_([fixture.workspace_id for fixture in scenario.workspaces])
                )
            )
        ).scalars()
        if tuple(existing):
            raise RuntimeError("Recovery workspace identifiers already exist.")

        await connection.execute(
            insert(workspaces),
            [
                {
                    "id": fixture.workspace_id,
                    "name": f"Recovery {scenario.run_id} {index}",
                    "is_local": False,
                    "created_at": now,
                    "updated_at": now,
                }
                for index, fixture in enumerate(scenario.workspaces, start=1)
            ],
        )
        await connection.execute(
            insert(documents),
            [
                {
                    "id": fixture.document_id,
                    "workspace_id": fixture.workspace_id,
                    "sha256": hashlib.sha256(fixture.document_bytes).hexdigest(),
                    "document_type": "pdf",
                    "media_type": "application/pdf",
                    "size_bytes": len(fixture.document_bytes),
                    "safe_filename": f"recovery-{index}.pdf",
                    "created_at": now,
                    "deleted_at": None,
                }
                for index, fixture in enumerate(scenario.workspaces, start=1)
            ],
        )
        await connection.execute(
            insert(discovery_analysis_sessions),
            [
                {
                    "id": fixture.analysis_session_id,
                    "workspace_id": fixture.workspace_id,
                    "owner_session_digest": None,
                    "created_by_user_id": None,
                    "query": f"recovery {scenario.run_id}",
                    "document_types": ["pdf"],
                    "include_domains": [],
                    "exclude_domains": [],
                    "tables_required": True,
                    "provider_search_ids": [],
                    "status": "completed",
                    "job_stage": "completed",
                    "creation_reason": "initial",
                    "candidate_count": 1,
                    "session_byte_limit": 1024 * 1024,
                    "bytes_downloaded": len(fixture.document_bytes),
                    "cancellation_requested": False,
                    "error_code": None,
                    "error_detail": None,
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": now + timedelta(days=1),
                    "completed_at": now,
                }
                for fixture in scenario.workspaces
            ],
        )
        await connection.execute(
            insert(candidate_analyses),
            [
                {
                    "id": fixture.candidate_id,
                    "workspace_id": fixture.workspace_id,
                    "session_id": fixture.analysis_session_id,
                    "ordinal": 0,
                    "source_url": f"https://example.invalid/{fixture.document_id}.pdf",
                    "title": "Recovery preview",
                    "description": None,
                    "document_type": "pdf",
                    "status": "ready",
                    "attempt_count": 1,
                    "available_at": now,
                    "claimed_by": None,
                    "lease_expires_at": None,
                    "bytes_downloaded": len(fixture.document_bytes),
                    "content_length": len(fixture.document_bytes),
                    "sha256": hashlib.sha256(fixture.document_bytes).hexdigest(),
                    "media_type": "application/pdf",
                    "safe_filename": "recovery.pdf",
                    "page_count": 1,
                    "analyzed_page_count": 1,
                    "table_count": 1,
                    "table_count_lower_bound": False,
                    "preview_page_num": 1,
                    "preview_width": 100,
                    "preview_height": 100,
                    "error_code": None,
                    "error_detail": None,
                    "error_retryable": None,
                    "promoted_document_id": None,
                    "created_at": now,
                    "updated_at": now,
                    "started_at": now,
                    "completed_at": now,
                    "expires_at": now + timedelta(days=1),
                }
                for fixture in scenario.workspaces
            ],
        )


async def register_objects(
    engine: AsyncEngine,
    store: S3ArtifactStore,
    scenario: RecoveryScenario,
    tmp_path: Path,
) -> None:
    repository = PostgresArtifactRepository(engine)
    now = datetime.now(UTC)
    for index, fixture in enumerate(scenario.workspaces, start=1):
        object_specs = (
            (
                fixture.document_key,
                fixture.document_bytes,
                "application/pdf",
                fixture.document_id,
                None,
                ArtifactKind.STORED_DOCUMENT,
                ArtifactLifecycle.PERSISTENT,
                None,
            ),
            (
                fixture.preview_key,
                fixture.preview_bytes,
                "image/png",
                None,
                fixture.candidate_id,
                ArtifactKind.PREVIEW_PNG,
                ArtifactLifecycle.TEMPORARY,
                now + timedelta(days=1),
            ),
        )
        for (
            key,
            content,
            media_type,
            document_id,
            candidate_id,
            kind,
            lifecycle,
            expiry,
        ) in object_specs:
            source = tmp_path / f"{index}-{kind.value}"
            await anyio.Path(source).write_bytes(content)
            metadata = StoredObjectMetadata(
                storage_key=key,
                media_type=media_type,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            pending = await repository.begin_upload(
                fixture.workspace_id,
                key,
                metadata,
                now=now,
            )
            await store.put_file(
                key,
                source,
                media_type=media_type,
                size_bytes=len(content),
                sha256=metadata.sha256,
            )
            await repository.finalize_reference(
                pending.id,
                workspace_id=fixture.workspace_id,
                document_id=document_id,
                candidate_analysis_id=candidate_id,
                kind=kind,
                lifecycle=lifecycle,
                expires_at=expiry,
                now=now,
            )


async def live_registry_rows(
    engine: AsyncEngine,
    scenario: RecoveryScenario,
) -> tuple[dict[str, Any], ...]:
    async with engine.connect() as connection:
        result = await connection.execute(
            select(
                artifact_objects.c.workspace_id,
                artifact_objects.c.storage_key,
                artifact_objects.c.media_type,
                artifact_objects.c.size_bytes,
                artifact_objects.c.sha256,
            ).where(
                artifact_objects.c.workspace_id.in_(
                    [fixture.workspace_id for fixture in scenario.workspaces]
                ),
                artifact_objects.c.state == ArtifactObjectState.AVAILABLE.value,
            )
        )
        return tuple(dict(row) for row in result.mappings())


def copy_registered_objects(
    client: S3Client,
    rows: tuple[dict[str, Any], ...],
    *,
    source_bucket: str,
    destination_bucket: str,
) -> None:
    for row in rows:
        key = str(row["storage_key"])
        client.copy_object(
            Bucket=destination_bucket,
            Key=key,
            CopySource={"Bucket": source_bucket, "Key": key},
        )
        response = client.head_object(Bucket=destination_bucket, Key=key)
        assert response["ContentLength"] == row["size_bytes"]
        assert response["Metadata"]["parserium-sha256"] == row["sha256"]


def empty_registered_objects(
    client: S3Client,
    rows: tuple[dict[str, Any], ...],
    *,
    bucket: str,
) -> None:
    for row in rows:
        client.delete_object(Bucket=bucket, Key=str(row["storage_key"]))
    for row in rows:
        with pytest.raises(ClientError) as captured:
            client.head_object(Bucket=bucket, Key=str(row["storage_key"]))
        assert captured.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


async def read_stream(stream: Any) -> bytes:
    return b"".join([chunk async for chunk in stream])


async def verify_restored_state(
    engine: AsyncEngine,
    client: S3Client,
    store: S3ArtifactStore,
    primary_bucket: str,
    backup_bucket: str,
    ca_bundle: Path,
    scenario: RecoveryScenario,
) -> None:
    rows = await live_registry_rows(engine, scenario)
    assert len(rows) == 4
    empty_registered_objects(client, rows, bucket=primary_bucket)
    copy_registered_objects(
        client,
        rows,
        source_bucket=backup_bucket,
        destination_bucket=primary_bucket,
    )

    for row in rows:
        metadata = await store.stat(str(row["storage_key"]))
        chunks: list[bytes] = []
        async with store.open_reader(metadata.storage_key) as reader:
            while chunk := await reader.read(4096):
                chunks.append(chunk)
        content = b"".join(chunks)
        assert len(content) == row["size_bytes"] == metadata.size_bytes
        assert hashlib.sha256(content).hexdigest() == row["sha256"] == metadata.sha256

    repository = PostgresArtifactRepository(engine)
    access = ArtifactAccessService(repository, store, signed_url_ttl_seconds=30)
    tls_context = ssl.create_default_context(cafile=str(ca_bundle))
    async with httpx.AsyncClient(verify=tls_context, trust_env=False) as http_client:
        for fixture in scenario.workspaces:
            usage_before = await repository.get_usage(fixture.workspace_id)
            assert usage_before.retained_bytes == fixture.expected_bytes
            assert usage_before.retained_objects == 2
            usage_after = await repository.reconcile_usage(
                fixture.workspace_id,
                now=datetime.now(UTC),
            )
            assert usage_after.retained_bytes - usage_before.retained_bytes == 0
            assert usage_after.retained_objects - usage_before.retained_objects == 0

            document = await access.open_document(
                fixture.workspace_id,
                fixture.document_id,
                "recovered.pdf",
            )
            assert isinstance(document, SignedArtifactDownload)
            response = await http_client.get(document.target)
            assert response.status_code == 200
            assert response.content == fixture.document_bytes

            preview = await access.open_preview(fixture.workspace_id, fixture.candidate_id)
            assert await read_stream(preview.body) == fixture.preview_bytes

    workspace_a, workspace_b = scenario.workspaces
    for owner, foreign in ((workspace_a, workspace_b), (workspace_b, workspace_a)):
        with pytest.raises(ArtifactNotFoundError):
            await access.open_document(
                owner.workspace_id,
                foreign.document_id,
                "foreign.pdf",
            )
        with pytest.raises(ArtifactNotFoundError):
            await access.open_preview(owner.workspace_id, foreign.candidate_id)


async def test_joint_database_and_s3_recovery(tmp_path: Path) -> None:
    phase = required_environment("TEST_RECOVERY_PHASE")
    if phase not in {"seed-and-backup", "empty-primary", "restore-and-verify"}:
        raise RuntimeError("The recovery phase is invalid.")
    scenario = RecoveryScenario.from_environment()
    client, store, primary_bucket, backup_bucket, ca_bundle = create_s3()
    engine = create_async_engine(database_url())
    try:
        if phase == "seed-and-backup":
            await seed_relational_records(engine, scenario)
            await register_objects(engine, store, scenario, tmp_path)
            rows = await live_registry_rows(engine, scenario)
            assert len(rows) == 4
            copy_registered_objects(
                client,
                rows,
                source_bucket=primary_bucket,
                destination_bucket=backup_bucket,
            )
        elif phase == "empty-primary":
            rows = await live_registry_rows(engine, scenario)
            assert len(rows) == 4
            empty_registered_objects(client, rows, bucket=primary_bucket)
        else:
            await verify_restored_state(
                engine,
                client,
                store,
                primary_bucket,
                backup_bucket,
                ca_bundle,
                scenario,
            )
    finally:
        await engine.dispose()
