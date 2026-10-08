from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from parserium_collector.features.storage.models import StoredObjectMetadata


class ArtifactReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class ArtifactStore(Protocol):
    async def put_file(
        self,
        storage_key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        size_bytes: int,
    ) -> StoredObjectMetadata: ...

    async def stat(self, storage_key: str) -> StoredObjectMetadata: ...

    def open_reader(self, storage_key: str) -> AbstractAsyncContextManager[ArtifactReader]: ...

    async def presign_get(
        self,
        storage_key: str,
        *,
        filename: str,
        media_type: str,
        ttl_seconds: int,
    ) -> str | None: ...

    async def delete(self, storage_key: str) -> None: ...

    async def probe(self, probe_key: str) -> None: ...
