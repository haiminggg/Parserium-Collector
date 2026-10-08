from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

import anyio

from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.analysis.fingerprint import fingerprint_request
from parserium_collector.features.analysis.models import DiscoveryClaim, DiscoverySelection
from parserium_collector.features.discovery.models import DocumentDiscoveryRequest
from parserium_collector.features.discovery.scoped import ScopedDiscoveryResult


class DiscoveryWorkerRepository(Protocol):
    async def recover_uncertain_discovery(self, now: datetime) -> bool: ...

    async def claim_discovery(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DiscoveryClaim | None: ...

    async def renew_discovery_lease(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def mark_provider_request_started(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool: ...

    async def is_discovery_cancellation_requested(
        self,
        session_id: UUID,
        worker_id: str,
    ) -> bool: ...

    async def complete_discovery(
        self,
        claim: DiscoveryClaim,
        result: ScopedDiscoveryResult,
        now: datetime,
        expires_at: datetime,
    ) -> bool: ...

    async def fail_discovery(
        self,
        session_id: UUID,
        worker_id: str,
        code: str,
        now: datetime,
    ) -> bool: ...

    async def cancel_discovery(
        self,
        session_id: UUID,
        worker_id: str,
        now: datetime,
    ) -> bool: ...


class PreparedDiscoveryService(Protocol):
    async def prepare_for_job(
        self,
        workspace_id: UUID,
        persisted: DiscoverySelection,
    ) -> DiscoverySelection: ...

    async def search_prepared(
        self,
        workspace_id: UUID,
        request: DocumentDiscoveryRequest,
        selection: DiscoverySelection,
        now: datetime,
    ) -> ScopedDiscoveryResult: ...


_PROVIDER_FAILURE_CODES = {
    "invalid_credentials": "provider_authentication_failed",
    "rate_limited": "provider_rate_limited",
    "timeout": "provider_timeout",
    "response_too_large": "provider_invalid_response",
    "incompatible_response": "provider_invalid_response",
    "blocked_destination": "connection_unavailable",
    "credential_unavailable": "connection_unavailable",
    "dns_failure": "connection_unavailable",
    "service_unavailable": "connection_unavailable",
    "tls_failure": "connection_unavailable",
}


@dataclass(frozen=True)
class DiscoveryJobWorker:
    repository: DiscoveryWorkerRepository
    discovery: PreparedDiscoveryService
    fingerprint_secret: bytes
    worker_id: str
    lease_seconds: int
    clock: Callable[[], datetime]

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0 or not self.worker_id:
            raise ValueError("Discovery worker lease and identity must be configured.")
        if len(self.fingerprint_secret) < 32:
            raise ValueError("The request fingerprint secret must contain at least 32 bytes.")

    async def run_once(self) -> bool:
        now = self.clock()
        recovered = await self.repository.recover_uncertain_discovery(now)
        claim = await self.repository.claim_discovery(
            self.worker_id,
            now,
            now + timedelta(seconds=self.lease_seconds),
        )
        if claim is None:
            return recovered

        session = claim.session
        if session.provider_request_started_at is not None:
            await self.repository.fail_discovery(
                session.id,
                self.worker_id,
                "provider_outcome_unknown",
                self.clock(),
            )
            return True
        if await self.repository.is_discovery_cancellation_requested(
            session.id,
            self.worker_id,
        ):
            await self.repository.cancel_discovery(session.id, self.worker_id, self.clock())
            return True

        try:
            current_selection = await self.discovery.prepare_for_job(
                session.workspace_id,
                claim.selection,
            )
        except Exception:
            await self.repository.fail_discovery(
                session.id,
                self.worker_id,
                "connection_unavailable",
                self.clock(),
            )
            return True

        expected = fingerprint_request(
            self.fingerprint_secret,
            session.workspace_id,
            claim.request,
            current_selection,
        )
        if expected != session.request_fingerprint:
            await self.repository.fail_discovery(
                session.id,
                self.worker_id,
                "connection_unavailable",
                self.clock(),
            )
            return True

        if not await self.repository.mark_provider_request_started(
            session.id,
            self.worker_id,
            self.clock(),
        ):
            if await self.repository.is_discovery_cancellation_requested(
                session.id,
                self.worker_id,
            ):
                await self.repository.cancel_discovery(session.id, self.worker_id, self.clock())
            return True

        try:
            result, lease_retained = await self._search_with_lease(
                claim,
                current_selection,
            )
        except FirecrawlAdapterError as error:
            await self.repository.fail_discovery(
                session.id,
                self.worker_id,
                _PROVIDER_FAILURE_CODES.get(error.code, "provider_invalid_response"),
                self.clock(),
            )
            return True
        except Exception:
            await self.repository.fail_discovery(
                session.id,
                self.worker_id,
                "provider_outcome_unknown",
                self.clock(),
            )
            return True

        if not lease_retained:
            return True
        if await self.repository.is_discovery_cancellation_requested(
            session.id,
            self.worker_id,
        ):
            await self.repository.cancel_discovery(session.id, self.worker_id, self.clock())
            return True
        await self.repository.complete_discovery(
            claim,
            result,
            self.clock(),
            session.expires_at,
        )
        return True

    async def _search_with_lease(
        self,
        claim: DiscoveryClaim,
        selection: DiscoverySelection,
    ) -> tuple[ScopedDiscoveryResult, bool]:
        stop = anyio.Event()
        lease_lost = anyio.Event()

        async def renew() -> None:
            interval = self.lease_seconds / 3
            while True:
                with anyio.move_on_after(interval):
                    await stop.wait()
                if stop.is_set():
                    return
                renewed_at = self.clock()
                if not await self.repository.renew_discovery_lease(
                    claim.session.id,
                    self.worker_id,
                    renewed_at,
                    renewed_at + timedelta(seconds=self.lease_seconds),
                ):
                    lease_lost.set()
                    return

        provider_error: FirecrawlAdapterError | None = None
        result: ScopedDiscoveryResult | None = None
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(renew)
            try:
                try:
                    result = await self.discovery.search_prepared(
                        claim.session.workspace_id,
                        claim.request,
                        selection,
                        self.clock(),
                    )
                except FirecrawlAdapterError as error:
                    provider_error = error
            finally:
                stop.set()
        if provider_error is not None:
            raise provider_error
        if result is None:
            raise RuntimeError("Discovery returned no result.")
        return result, not lease_lost.is_set()
