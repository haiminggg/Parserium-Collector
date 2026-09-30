import ipaddress
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit

from parserium_collector.features.firecrawl_connections.errors import (
    RemoteEndpointRejectedError,
)

FIRECRAWL_CLOUD_ORIGIN = "https://api.firecrawl.dev"
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _validated_ports(allowed_ports: Iterable[int]) -> tuple[int, ...]:
    ports = tuple(allowed_ports)
    if (
        not ports
        or len(ports) != len(set(ports))
        or any(isinstance(port, bool) or port < 1 or port > 65535 for port in ports)
    ):
        raise ValueError("Remote Firecrawl ports must be unique values from 1 through 65535.")
    return ports


def _normalize_hostname(hostname: str) -> str:
    candidate = hostname.removesuffix(".")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise RemoteEndpointRejectedError

    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as error:
        raise RemoteEndpointRejectedError from error
    labels = normalized.split(".")
    if (
        not normalized
        or len(normalized) > 253
        or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise RemoteEndpointRejectedError
    return normalized


def normalize_remote_origin(
    value: str,
    *,
    allowed_ports: Iterable[int] = (443,),
) -> str:
    ports = _validated_ports(allowed_ports)
    if not value or "\\" in value or any(unicodedata.category(char) == "Cc" for char in value):
        raise RemoteEndpointRejectedError
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise RemoteEndpointRejectedError from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.hostname is None
    ):
        raise RemoteEndpointRejectedError

    hostname = _normalize_hostname(parsed.hostname)
    effective_port = port or 443
    if effective_port not in ports:
        raise RemoteEndpointRejectedError
    suffix = "" if effective_port == 443 else f":{effective_port}"
    return f"https://{hostname}{suffix}"
