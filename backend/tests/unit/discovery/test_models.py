import pytest
from pydantic import ValidationError

from parserium_collector.features.discovery.models import (
    DocumentDiscoveryRequest,
    DocumentType,
)


def test_discovery_request_normalizes_query_types_and_domains() -> None:
    request = DocumentDiscoveryRequest(
        query="  bank investment reports  ",
        document_types=(DocumentType.PDF, DocumentType.PDF, DocumentType.DOCX),
        include_domains=("Example.COM", "example.com"),
    )

    assert request.query == "bank investment reports"
    assert request.document_types == (DocumentType.PDF, DocumentType.DOCX)
    assert request.include_domains == ("example.com",)


def test_discovery_request_rejects_empty_document_types() -> None:
    with pytest.raises(ValidationError, match="document type"):
        DocumentDiscoveryRequest(query="bank reports", document_types=())


def test_discovery_request_rejects_mixed_domain_modes() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        DocumentDiscoveryRequest(
            query="bank reports",
            include_domains=("example.com",),
            exclude_domains=("example.org",),
        )


@pytest.mark.parametrize("domain", ["https://example.com", "example.com/path", "bad domain"])
def test_discovery_request_rejects_non_host_domain_hints(domain: str) -> None:
    with pytest.raises(ValidationError, match="domain"):
        DocumentDiscoveryRequest(query="bank reports", include_domains=(domain,))
