import base64
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import AsyncMock, Mock

import anyio
import pytest

import parserium_collector.worker as worker_module
from parserium_collector.features.storage.factory import StorageRuntime
from parserium_collector.settings import Settings
from parserium_collector.worker import run, serve_worker


class BlockingAcquisitionWorker:
    def __init__(self) -> None:
        self.release = anyio.Event()
        self.run_calls = 0

    async def run_once(self) -> bool:
        self.run_calls += 1
        await self.release.wait()
        return False


class IdleAnalysisWorker:
    def __init__(self) -> None:
        self.run_calls = 0

    async def run_once(self) -> bool:
        self.run_calls += 1
        return False


async def test_heartbeats_continue_while_acquisition_work_is_blocked() -> None:
    analysis_worker = IdleAnalysisWorker()
    acquisition_worker = BlockingAcquisitionWorker()
    stop = anyio.Event()
    heartbeat_count = 0

    async def heartbeat() -> None:
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count == 2:
            acquisition_worker.release.set()
            stop.set()

    async def event_waiter(event: anyio.Event, interval: float) -> bool:
        await anyio.lowlevel.checkpoint()
        return event.is_set()

    await serve_worker(
        analysis_worker=analysis_worker,
        acquisition_worker=acquisition_worker,
        heartbeat=heartbeat,
        heartbeat_seconds=10.0,
        idle_seconds=1.0,
        stop=stop,
        waiter=event_waiter,
    )

    assert heartbeat_count == 2
    assert analysis_worker.run_calls == 1
    assert acquisition_worker.run_calls == 1


async def test_analysis_work_takes_priority_over_acquisition_work() -> None:
    calls: list[str] = []
    stop = anyio.Event()

    class AnalysisWorkerWithOneJob:
        async def run_once(self) -> bool:
            calls.append("analysis")
            stop.set()
            return True

    class AcquisitionWorkerThatMustRemainIdle:
        async def run_once(self) -> bool:
            calls.append("acquisition")
            return False

    async def heartbeat() -> None:
        await anyio.lowlevel.checkpoint()

    async def event_waiter(event: anyio.Event, interval: float) -> bool:
        await anyio.lowlevel.checkpoint()
        return event.is_set()

    await serve_worker(
        analysis_worker=AnalysisWorkerWithOneJob(),
        acquisition_worker=AcquisitionWorkerThatMustRemainIdle(),
        heartbeat=heartbeat,
        heartbeat_seconds=10.0,
        idle_seconds=1.0,
        stop=stop,
        waiter=event_waiter,
    )

    assert calls == ["analysis"]


def test_worker_waiter_contract_is_async() -> None:
    waiter: Callable[[anyio.Event, float], Awaitable[bool]]

    async def implementation(event: anyio.Event, interval: float) -> bool:
        return event.is_set()

    waiter = implementation
    assert waiter is implementation


async def test_maintenance_continues_while_acquisition_is_blocked() -> None:
    analysis_worker = IdleAnalysisWorker()
    acquisition_worker = BlockingAcquisitionWorker()
    stop = anyio.Event()

    class MaintenanceWorker:
        def __init__(self) -> None:
            self.run_calls = 0

        async def run_once(self) -> bool:
            self.run_calls += 1
            acquisition_worker.release.set()
            stop.set()
            return True

    maintenance_worker = MaintenanceWorker()

    async def heartbeat() -> None:
        await anyio.lowlevel.checkpoint()

    async def event_waiter(event: anyio.Event, interval: float) -> bool:
        await anyio.lowlevel.checkpoint()
        return event.is_set()

    await serve_worker(
        analysis_worker=analysis_worker,
        acquisition_worker=acquisition_worker,
        maintenance_worker=maintenance_worker,
        heartbeat=heartbeat,
        heartbeat_seconds=10.0,
        maintenance_seconds=30.0,
        idle_seconds=1.0,
        stop=stop,
        waiter=event_waiter,
    )

    assert maintenance_worker.run_calls == 1
    assert acquisition_worker.run_calls == 1


async def test_maintenance_failure_is_safely_logged_and_retried(
    caplog: object,
) -> None:
    analysis_worker = IdleAnalysisWorker()
    acquisition_worker = BlockingAcquisitionWorker()
    stop = anyio.Event()

    class MaintenanceWorker:
        def __init__(self) -> None:
            self.run_calls = 0

        async def run_once(self) -> bool:
            self.run_calls += 1
            if self.run_calls == 1:
                raise RuntimeError("sensitive provider response")
            acquisition_worker.release.set()
            stop.set()
            return True

    maintenance_worker = MaintenanceWorker()

    async def heartbeat() -> None:
        await anyio.lowlevel.checkpoint()

    async def event_waiter(event: anyio.Event, interval: float) -> bool:
        await anyio.lowlevel.checkpoint()
        return event.is_set()

    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        await serve_worker(
            analysis_worker=analysis_worker,
            acquisition_worker=acquisition_worker,
            maintenance_worker=maintenance_worker,
            heartbeat=heartbeat,
            heartbeat_seconds=10.0,
            maintenance_seconds=30.0,
            idle_seconds=1.0,
            stop=stop,
            waiter=event_waiter,
        )

    assert maintenance_worker.run_calls == 2
    assert "Artifact maintenance pass failed." in caplog.text  # type: ignore[attr-defined]
    assert "sensitive provider response" not in caplog.text  # type: ignore[attr-defined]


def fingerprint_secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "fingerprint-secret"
    path.write_bytes(b"f" * 32)
    return path


def hosted_worker_settings(tmp_path: Path) -> Settings:
    oidc_secret = tmp_path / "oidc-secret"
    oidc_secret.write_text(token_urlsafe(48), encoding="utf-8")
    wrapping_key = tmp_path / "wrapping-key"
    wrapping_key.write_bytes(base64.b64encode(b"k" * 32))
    return Settings(
        deployment_mode="hosted",
        public_origin="https://app.parserium.test",
        allowed_hosts=("app.parserium.test",),
        allowed_origins=("https://app.parserium.test",),
        session_cookie_secure=True,
        oidc_issuer="https://identity.parserium.test",
        oidc_client_id="parserium-web",
        oidc_client_secret_file=oidc_secret,
        oidc_redirect_uri="https://app.parserium.test/api/v1/auth/callback",
        storage_backend="s3",
        storage_endpoint="https://minio.parserium.test",
        storage_region="us-east-1",
        storage_bucket="parserium-artifacts",
        scratch_root=tmp_path / "scratch",
        credential_encryption_key_file=wrapping_key,
        credential_encryption_key_id="hosted-v1",
        discovery_fingerprint_secret_file=fingerprint_secret_file(tmp_path),
    )


@pytest.mark.parametrize("hosted", [False, True])
async def test_worker_constructs_one_storage_runtime_shared_by_all_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hosted: bool,
) -> None:
    export_root = tmp_path / "exports"
    export_root.mkdir()
    settings = (
        hosted_worker_settings(tmp_path)
        if hosted
        else Settings(
            storage_root=tmp_path / "durable",
            scratch_root=tmp_path / "scratch",
            export_root=export_root,
            discovery_fingerprint_secret_file=fingerprint_secret_file(tmp_path),
        )
    )
    engine = AsyncMock()
    artifact_repository = Mock(name="artifact-repository")
    acquisition_repository = Mock(name="acquisition-repository")
    analysis_repository = Mock(name="analysis-repository")
    artifact_store = Mock(name="artifact-store")
    scratch_storage = Mock(name="scratch-storage")
    export_storage = None if hosted else Mock(name="export-storage")
    runtime = StorageRuntime(
        artifact_store=artifact_store,
        scratch_storage=scratch_storage,
        export_storage=export_storage,
    )
    runtime_calls: list[Settings] = []
    worker_arguments: dict[str, dict[str, object]] = {}

    class ClosableRunner:
        async def run_once(self) -> bool:
            return False

        async def aclose(self) -> None:
            return None

    def capture_worker(name: str):
        def constructor(**kwargs: object) -> ClosableRunner:
            worker_arguments[name] = kwargs
            return ClosableRunner()

        return constructor

    def runtime_factory(actual: Settings) -> StorageRuntime:
        runtime_calls.append(actual)
        return runtime

    async def fake_serve_worker(**kwargs: object) -> None:
        worker_arguments["serve"] = kwargs

    monkeypatch.setattr(worker_module, "create_engine", lambda actual: engine)
    monkeypatch.setattr(worker_module, "create_storage_runtime", runtime_factory)
    monkeypatch.setattr(
        worker_module,
        "PostgresArtifactRepository",
        lambda actual: artifact_repository,
    )
    monkeypatch.setattr(
        worker_module,
        "PostgresAcquisitionRepository",
        lambda actual, artifacts: acquisition_repository,
    )
    monkeypatch.setattr(
        worker_module,
        "PostgresAnalysisRepository",
        lambda actual, artifacts: analysis_repository,
    )
    monkeypatch.setattr(worker_module, "AcquisitionWorker", capture_worker("acquisition"))
    monkeypatch.setattr(worker_module, "AnalysisWorker", capture_worker("analysis"))
    monkeypatch.setattr(
        worker_module,
        "ArtifactMaintenanceService",
        capture_worker("maintenance"),
    )
    monkeypatch.setattr(worker_module, "serve_worker", fake_serve_worker)

    await run(settings)

    assert runtime_calls == [settings]
    assert worker_arguments["acquisition"]["artifact_store"] is artifact_store
    assert worker_arguments["acquisition"]["scratch_storage"] is scratch_storage
    assert worker_arguments["acquisition"]["export_storage"] is export_storage
    assert worker_arguments["analysis"]["artifact_store"] is artifact_store
    assert worker_arguments["analysis"]["scratch_storage"] is scratch_storage
    assert worker_arguments["maintenance"]["artifact_store"] is artifact_store
    assert worker_arguments["maintenance"]["scratch_storage"] is scratch_storage
    assert worker_arguments["serve"]["maintenance_worker"] is not None
    engine.dispose.assert_awaited_once()
