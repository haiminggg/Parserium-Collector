from ipaddress import ip_address

import pytest

from parserium_collector.features.acquisition.errors import (
    BlockedDestinationError,
    UnsupportedSchemeError,
)
from parserium_collector.features.acquisition.network_policy import NetworkPolicy


class StaticResolver:
    def __init__(self, answers: dict[str, tuple[str, ...]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return self.answers.get(hostname, ())


def policy_for(
    answers: dict[str, tuple[str, ...]],
    *,
    private_allowlist: tuple[str, ...] = (),
) -> NetworkPolicy:
    return NetworkPolicy(
        resolver=StaticResolver(answers),
        allowed_public_ports=(80, 443),
        private_allowlist=private_allowlist,
    )


async def test_public_https_target_is_normalized_and_approved() -> None:
    resolver = StaticResolver({"example.com": ("93.184.216.34",)})
    policy = NetworkPolicy(
        resolver=resolver,
        allowed_public_ports=(80, 443),
        private_allowlist=(),
    )

    target = await policy.approve_url("HTTPS://Example.COM./reports/annual.pdf")

    assert target.scheme == "https"
    assert target.hostname == "example.com"
    assert target.port == 443
    assert target.addresses == (ip_address("93.184.216.34"),)
    assert resolver.calls == [("example.com", 443)]


async def test_public_ipv4_and_ipv6_literals_are_approved_without_dns() -> None:
    resolver = StaticResolver({})
    policy = NetworkPolicy(
        resolver=resolver,
        allowed_public_ports=(80, 443),
        private_allowlist=(),
    )

    ipv4 = await policy.approve_url("https://93.184.216.34/report.pdf")
    ipv6 = await policy.approve_url("https://[2606:2800:220:1:248:1893:25c8:1946]/report.pdf")

    assert ipv4.addresses == (ip_address("93.184.216.34"),)
    assert ipv6.addresses == (ip_address("2606:2800:220:1:248:1893:25c8:1946"),)
    assert resolver.calls == []


async def test_non_http_schemes_are_rejected() -> None:
    policy = policy_for({"example.com": ("93.184.216.34",)})

    with pytest.raises(UnsupportedSchemeError):
        await policy.approve_url("ftp://example.com/report.pdf")


@pytest.mark.parametrize(
    "url",
    (
        "https://user@example.com/report.pdf",
        "https://user:secret@example.com/report.pdf",
    ),
)
async def test_urls_with_credentials_are_rejected(url: str) -> None:
    policy = policy_for({"example.com": ("93.184.216.34",)})

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url(url)


async def test_unapproved_public_port_is_rejected_before_dns() -> None:
    resolver = StaticResolver({"example.com": ("93.184.216.34",)})
    policy = NetworkPolicy(
        resolver=resolver,
        allowed_public_ports=(80, 443),
        private_allowlist=(),
    )

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url("https://example.com:8443/report.pdf")

    assert resolver.calls == []


@pytest.mark.parametrize(
    "address",
    (
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
        "192.0.2.1",
        "fd00::1",
        "::1",
        "fe80::1",
        "ff02::1",
        "::",
        "100::1",
    ),
)
async def test_non_public_addresses_are_blocked_by_default(address: str) -> None:
    policy = policy_for({"target.example": (address,)})

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url("https://target.example/report.pdf")


async def test_one_prohibited_dns_answer_blocks_the_entire_target() -> None:
    policy = policy_for(
        {"mixed.example": ("93.184.216.34", "10.0.0.8")},
    )

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url("https://mixed.example/report.pdf")


async def test_private_target_requires_an_exact_allowlisted_host_and_port() -> None:
    answers = {
        "internal.example": ("10.0.0.8",),
        "sub.internal.example": ("10.0.0.9",),
        "evilinternal.example": ("10.0.0.10",),
    }
    policy = policy_for(
        answers,
        private_allowlist=("internal.example:8443",),
    )

    approved = await policy.approve_url("https://internal.example:8443/report.pdf")

    assert approved.addresses == (ip_address("10.0.0.8"),)
    for url in (
        "https://internal.example/report.pdf",
        "https://sub.internal.example:8443/report.pdf",
        "https://evilinternal.example:8443/report.pdf",
    ):
        with pytest.raises(BlockedDestinationError):
            await policy.approve_url(url)


async def test_allowlisted_loopback_is_allowed_but_link_local_is_still_blocked() -> None:
    policy = policy_for(
        {
            "local-origin.example": ("127.0.0.1",),
            "metadata.example": ("169.254.169.254",),
        },
        private_allowlist=(
            "local-origin.example:8080",
            "metadata.example:8080",
        ),
    )

    target = await policy.approve_url("http://local-origin.example:8080/report.pdf")
    assert target.addresses == (ip_address("127.0.0.1"),)

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url("http://metadata.example:8080/report.pdf")


async def test_unicode_and_trailing_dot_hostnames_use_one_ascii_identity() -> None:
    resolver = StaticResolver({"xn--bcher-kva.example": ("10.0.0.8",)})
    policy = NetworkPolicy(
        resolver=resolver,
        allowed_public_ports=(80, 443),
        private_allowlist=("BÜCHER.example.:8443",),
    )

    target = await policy.approve_url("https://bücher.example.:8443/report.pdf")

    assert target.hostname == "xn--bcher-kva.example"
    assert resolver.calls == [("xn--bcher-kva.example", 8443)]


async def test_empty_dns_result_is_rejected() -> None:
    policy = policy_for({})

    with pytest.raises(BlockedDestinationError):
        await policy.approve_url("https://missing.example/report.pdf")
