import logging
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import AnyHttpUrl

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
    SearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.discovery.models import (
    DocumentCandidate,
    DocumentDiscoveryRequest,
    DocumentDiscoveryResponse,
    DocumentType,
)

LOGGER = logging.getLogger(__name__)


class MetadataSearchProvider(Protocol):
    async def probe_metadata_search(
        self,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult: ...


class DiscoveryService:
    def __init__(self, provider: MetadataSearchProvider) -> None:
        self._provider = provider

    async def search(self, request: DocumentDiscoveryRequest) -> DocumentDiscoveryResponse:
        search_ids: list[str] = []
        buckets: list[list[DocumentCandidate]] = []
        seen_urls: set[str] = set()
        rejected = 0

        for document_type in request.document_types:
            try:
                result = await self._provider.probe_metadata_search(
                    MetadataSearchRequest(
                        query=f"{request.query} filetype:{document_type.value}",
                        limit=request.limit,
                        include_domains=request.include_domains,
                        exclude_domains=request.exclude_domains,
                    )
                )
            except FirecrawlAdapterError as error:
                LOGGER.warning(
                    "Document metadata search failed: document_type=%s code=%s retryable=%s",
                    document_type.value,
                    error.code,
                    error.retryable,
                )
                raise
            search_ids.append(result.search_id)
            bucket, rejected_count = self._direct_candidates(
                result.results,
                document_type,
                seen_urls,
            )
            buckets.append(bucket)
            rejected += rejected_count

        return DocumentDiscoveryResponse(
            provider_search_ids=search_ids,
            candidates=self._round_robin(buckets, request.limit),
            rejected_non_document_results=rejected,
        )

    @staticmethod
    def _direct_candidates(
        results: Sequence[SearchResult],
        expected_type: DocumentType,
        seen_urls: set[str],
    ) -> tuple[list[DocumentCandidate], int]:
        candidates: list[DocumentCandidate] = []
        rejected = 0
        for result in results:
            parts = urlsplit(str(result.url))
            suffix = unquote(parts.path).lower().rsplit(".", maxsplit=1)[-1]
            if suffix != expected_type.value:
                rejected += 1
                continue
            normalized = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
            dedupe_key = urlunsplit(
                (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
            )
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            candidates.append(
                DocumentCandidate(
                    url=AnyHttpUrl(normalized),
                    title=result.title,
                    description=result.description,
                    document_type=expected_type,
                )
            )
        return candidates, rejected

    @staticmethod
    def _round_robin(
        buckets: Sequence[Sequence[DocumentCandidate]],
        limit: int,
    ) -> list[DocumentCandidate]:
        selected: list[DocumentCandidate] = []
        positions = [0] * len(buckets)
        while len(selected) < limit:
            added = False
            for index, bucket in enumerate(buckets):
                if positions[index] >= len(bucket):
                    continue
                selected.append(bucket[positions[index]])
                positions[index] += 1
                added = True
                if len(selected) == limit:
                    break
            if not added:
                break
        return selected
