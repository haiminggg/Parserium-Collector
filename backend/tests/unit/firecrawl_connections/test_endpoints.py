import pytest

from parserium_collector.features.firecrawl_connections.endpoints import (
    FIRECRAWL_CLOUD_ORIGIN,
    normalize_remote_origin,
)
from parserium_collector.features.firecrawl_connections.errors import (
    RemoteEndpointRejectedError,
)


def test_cloud_origin_is_fixed() -> None:
    assert FIRECRAWL_CLOUD_ORIGIN == "https://api.firecrawl.dev"


def test_remote_origin_is_normalized_without_a_path() -> None:
    assert normalize_remote_origin("HTTPS://Firecrawl.Example.:443/") == (
        "https://firecrawl.example"
    )


def test_remote_origin_supports_an_explicit_approved_secure_port() -> None:
    assert (
        normalize_remote_origin(
            "https://firecrawl.example:9443",
            allowed_ports=(443, 9443),
        )
        == "https://firecrawl.example:9443"
    )


def test_remote_origin_normalizes_an_idna_hostname() -> None:
    assert normalize_remote_origin("https://bücher.example") == ("https://xn--bcher-kva.example")


@pytest.mark.parametrize(
    "value",
    [
        "http://firecrawl.example",
        "ftp://firecrawl.example",
        "https://127.0.0.1",
        "https://8.8.8.8",
        "https://[::1]",
        "https://user:secret@firecrawl.example",
        "https://firecrawl.example/v2",
        "https://firecrawl.example?query=yes",
        "https://firecrawl.example#fragment",
        "https://bad_host.example",
        "https://-bad.example",
        "https://bad..example",
        "https://firecrawl.example:8443",
        "https://firecrawl.example:99999",
        "https://firecrawl.example\\@blocked.example",
        "https://*.example",
        "",
    ],
)
def test_remote_origin_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(RemoteEndpointRejectedError):
        normalize_remote_origin(value)


@pytest.mark.parametrize("allowed_ports", [(), (0,), (65536,), (443, 443)])
def test_remote_origin_rejects_an_invalid_operator_port_policy(
    allowed_ports: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError):
        normalize_remote_origin(
            "https://firecrawl.example",
            allowed_ports=allowed_ports,
        )
