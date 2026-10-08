import re
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Protocol
from urllib.parse import urlsplit

import anyio

from parserium_collector.features.acquisition.errors import (
    BlockedDestinationError,
    DestinationResolutionError,
    UnsupportedSchemeError,
)

IPAddress = IPv4Address | IPv6Address
HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class AddressResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class AnyIOAddressResolver:
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        try:
            results = await anyio.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise DestinationResolutionError("The destination could not be resolved.") from error
        return tuple(result[4][0] for result in results)


@dataclass(frozen=True)
class ApprovedEndpoint:
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]


@dataclass(frozen=True)
class ApprovedTarget:
    scheme: str
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]


class NetworkPolicy:
    def __init__(
        self,
        *,
        resolver: AddressResolver | None = None,
        allowed_public_ports: Iterable[int] = (80, 443),
        private_allowlist: Iterable[str] = (),
    ) -> None:
        self._resolver = resolver or AnyIOAddressResolver()
        self._allowed_public_ports = frozenset(allowed_public_ports)
        if not self._allowed_public_ports or any(
            port < 1 or port > 65_535 for port in self._allowed_public_ports
        ):
            raise ValueError("Public destination ports must be between 1 and 65535.")
        self._private_allowlist = frozenset(
            self._parse_allowlist_entry(entry) for entry in private_allowlist
        )

    async def approve_url(self, url: str) -> ApprovedTarget:
        try:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            if scheme not in {"http", "https"}:
                raise UnsupportedSchemeError("Only HTTP and HTTPS document URLs are supported.")
            if parts.username is not None or parts.password is not None:
                raise BlockedDestinationError("Document URLs cannot include credentials.")
            if parts.hostname is None:
                raise BlockedDestinationError("The document URL must include a hostname.")
            port = parts.port or (443 if scheme == "https" else 80)
        except (UnicodeError, ValueError) as error:
            raise BlockedDestinationError("The document URL is invalid.") from error

        endpoint = await self.approve_connection(parts.hostname, port)
        return ApprovedTarget(
            scheme=scheme,
            hostname=endpoint.hostname,
            port=endpoint.port,
            addresses=endpoint.addresses,
        )

    async def approve_connection(self, hostname: str, port: int) -> ApprovedEndpoint:
        normalized_hostname = self._normalize_hostname(hostname)
        allowlisted = (normalized_hostname, port) in self._private_allowlist
        if port not in self._allowed_public_ports and not allowlisted:
            raise BlockedDestinationError("The destination port is not allowed.")

        literal_address = self._parse_ip_literal(normalized_hostname)
        if literal_address is None:
            raw_addresses = await self._resolver.resolve(normalized_hostname, port)
            addresses = self._parse_addresses(raw_addresses)
        else:
            addresses = (literal_address,)

        if not addresses:
            raise BlockedDestinationError("The destination did not resolve to an address.")
        if any(not self._address_is_allowed(address, allowlisted) for address in addresses):
            raise BlockedDestinationError("The destination resolved to a prohibited address.")
        return ApprovedEndpoint(
            hostname=normalized_hostname,
            port=port,
            addresses=addresses,
        )

    @classmethod
    def _parse_allowlist_entry(cls, entry: str) -> tuple[str, int]:
        try:
            parts = urlsplit(f"//{entry.strip()}")
            if (
                parts.hostname is None
                or parts.port is None
                or parts.username is not None
                or parts.password is not None
                or parts.path
                or parts.query
                or parts.fragment
            ):
                raise ValueError
            return cls._normalize_hostname(parts.hostname), parts.port
        except (UnicodeError, ValueError) as error:
            raise ValueError(
                "Private allowlist entries must use an exact hostname:port value."
            ) from error

    @staticmethod
    def _normalize_hostname(hostname: str) -> str:
        candidate = hostname.removesuffix(".").lower()
        if not candidate or candidate.endswith(".") or "%" in candidate:
            raise BlockedDestinationError("The destination hostname is invalid.")
        literal_address = NetworkPolicy._parse_ip_literal(candidate)
        if literal_address is not None:
            return str(literal_address)
        try:
            ascii_hostname = candidate.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise BlockedDestinationError("The destination hostname is invalid.") from error
        if len(ascii_hostname) > 253 or any(
            not HOST_LABEL_PATTERN.fullmatch(label) for label in ascii_hostname.split(".")
        ):
            raise BlockedDestinationError("The destination hostname is invalid.")
        return ascii_hostname

    @staticmethod
    def _parse_ip_literal(hostname: str) -> IPAddress | None:
        try:
            return ip_address(hostname)
        except ValueError:
            return None

    @staticmethod
    def _parse_addresses(addresses: Iterable[str]) -> tuple[IPAddress, ...]:
        parsed: list[IPAddress] = []
        seen: set[IPAddress] = set()
        for value in addresses:
            try:
                address = ip_address(value)
            except ValueError as error:
                raise BlockedDestinationError(
                    "The destination resolver returned an invalid address."
                ) from error
            if address not in seen:
                seen.add(address)
                parsed.append(address)
        return tuple(parsed)

    @staticmethod
    def _effective_address(address: IPAddress) -> IPAddress:
        if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
            return address.ipv4_mapped
        return address

    @classmethod
    def _address_is_allowed(cls, address: IPAddress, allowlisted: bool) -> bool:
        effective = cls._effective_address(address)
        if (
            effective.is_link_local
            or effective.is_multicast
            or effective.is_unspecified
            or effective.is_reserved
        ):
            return False
        if allowlisted:
            return effective.is_global or effective.is_private or effective.is_loopback
        return effective.is_global
