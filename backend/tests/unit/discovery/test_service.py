import logging

import pytest

from parserium_collector.adapters.firecrawl.contracts import (
    MetadataSearchRequest,
    MetadataSearchResult,
    SearchResult,
)
from parserium_collector.adapters.firecrawl.errors import FirecrawlAdapterError
from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentType,
)
from parserium_collector.features.discovery.service import DiscoveryService


class RecordingSearchProvider:
    """Minimal metadata-search test double with no network access."""

    def __init__(self) -> None:
        self.requests: list[MetadataSearchRequest] = []

    async def probe_metadata_search(
        self,
        request: MetadataSearchRequest,
    ) -> MetadataSearchResult:
        self.requests.append(request)
        if request.query.endswith("filetype:pdf"):
            return MetadataSearchResult(
                search_id="pdf-search",
                results=[
                    SearchResult(
                        url="https://bank.example/reports/fund.PDF#page=4",
                        title="Fund report",
                    ),
                    SearchResult(
                        url="https://bank.example/reports",
                        title="Reports landing page",
                    ),
                ],
            )
        return MetadataSearchResult(
            search_id="docx-search",
            results=[
                SearchResult(
                    url="https://bank.example/reports/terms.docx?download=1",
                    title="Terms",
                ),
                SearchResult(
                    url="https://bank.example/reports/other.pdf",
                    title="Wrong result type",
                ),
            ],
        )


async def test_search_filters_direct_documents_and_merges_types_fairly() -> None:
    provider = RecordingSearchProvider()
    service = DiscoveryService(provider)

    result = await service.search(
        DocumentDiscoveryRequest(
            query="bank investment",
            limit=10,
            document_types=(DocumentType.PDF, DocumentType.DOCX),
            include_domains=("bank.example",),
        )
    )

    assert [request.query for request in provider.requests] == [
        "bank investment filetype:pdf",
        "bank investment filetype:docx",
    ]
    assert all(request.include_domains == ("bank.example",) for request in provider.requests)
    assert result.provider_search_ids == ["pdf-search", "docx-search"]
    assert [candidate.document_type for candidate in result.candidates] == [
        DocumentType.PDF,
        DocumentType.DOCX,
    ]
    assert str(result.candidates[0].url) == "https://bank.example/reports/fund.PDF"
    assert str(result.candidates[1].url) == ("https://bank.example/reports/terms.docx?download=1")
    assert result.rejected_non_document_results == 2


async def test_search_deduplicates_candidates_and_enforces_total_limit() -> None:
    class DuplicateSearchProvider:
        """Minimal metadata-search test double returning the same URL."""

        async def probe_metadata_search(
            self,
            request: MetadataSearchRequest,
        ) -> MetadataSearchResult:
            suffix = request.query.rsplit(":", maxsplit=1)[-1]
            return MetadataSearchResult(
                search_id=f"{suffix}-search",
                results=[
                    SearchResult(url=f"https://bank.example/report.{suffix}", title="One"),
                    SearchResult(url=f"https://bank.example/report.{suffix}", title="Duplicate"),
                ],
            )

    service = DiscoveryService(DuplicateSearchProvider())

    result = await service.search(
        DocumentDiscoveryRequest(
            query="bank investment",
            limit=1,
            document_types=(DocumentType.PDF, DocumentType.DOCX),
        )
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].document_type is DocumentType.PDF


async def test_search_logs_safe_stage_diagnostics_and_preserves_provider_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_query = "confidential acquisition targets"
    provider_error = FirecrawlAdapterError(code="schema_mismatch", retryable=False)

    class FailingSearchProvider:
        """Minimal metadata-search failure test double."""

        async def probe_metadata_search(
            self,
            request: MetadataSearchRequest,
        ) -> MetadataSearchResult:
            del request
            raise provider_error

    caplog.set_level(
        logging.WARNING,
        logger="parserium_collector.features.discovery.service",
    )
    service = DiscoveryService(FailingSearchProvider())

    with pytest.raises(FirecrawlAdapterError) as raised:
        await service.search(
            DocumentDiscoveryRequest(
                query=private_query,
                document_types=(DocumentType.PDF,),
            )
        )

    assert raised.value is provider_error
    assert caplog.messages == [
        "Document metadata search failed: document_type=pdf code=schema_mismatch retryable=False"
    ]
    assert private_query not in caplog.text
