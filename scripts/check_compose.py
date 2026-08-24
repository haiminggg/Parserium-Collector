import argparse
import json
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"Compose policy failed: {message}")


def network_names(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", {})
    if not isinstance(networks, dict):
        fail("service networks must use rendered object form")
    return set(networks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    document: dict[str, Any] = json.loads(args.config.read_text(encoding="utf-8-sig"))
    services = document.get("services", {})
    required_names = {"api", "db", "migrate", "storage-init", "worker"}
    if not isinstance(services, dict) or set(services) != required_names:
        actual = sorted(services) if isinstance(services, dict) else []
        fail(f"expected services {sorted(required_names)}, got {actual}")

    published: list[tuple[str, dict[str, Any]]] = []
    for name, service_value in services.items():
        if not isinstance(service_value, dict):
            fail(f"{name} is not a rendered service object")
        service: dict[str, Any] = service_value
        for port in service.get("ports", []) or []:
            if not isinstance(port, dict):
                fail(f"{name} has an invalid rendered port")
            published.append((name, port))
        image = str(service.get("image", ""))
        if "build" not in service and "@sha256:" not in image:
            fail(f"{name} uses an external image without an immutable digest")

    if len(published) != 1:
        fail("exactly one port must be published")
    service_name, port = published[0]
    if service_name != "api":
        fail("only api may publish a port")
    if port.get("host_ip") != "127.0.0.1":
        fail("api port must bind to 127.0.0.1")
    if str(port.get("published")) != "8080" or str(port.get("target")) != "8080":
        fail("api port mapping must be 127.0.0.1:8080 to container 8080")

    for name in ("api", "migrate", "storage-init", "worker"):
        service = services[name]
        if service.get("read_only") is not True:
            fail(f"{name} root filesystem is not read-only")
        if "ALL" not in (service.get("cap_drop") or []):
            fail(f"{name} does not drop all capabilities")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            fail(f"{name} does not set no-new-privileges")

    if "no-new-privileges:true" not in (services["db"].get("security_opt") or []):
        fail("db does not set no-new-privileges")

    networks = document.get("networks", {})
    if not isinstance(networks, dict):
        fail("rendered networks must be an object")
    if set(networks) != {"edge", "private"}:
        fail("exactly the edge and private networks must exist")
    private = networks.get("private", {})
    if not isinstance(private, dict) or private.get("internal") is not True:
        fail("private network is not internal")
    edge = networks.get("edge", {})
    if not isinstance(edge, dict) or edge.get("internal") is True:
        fail("edge network must support loopback port publishing")

    if network_names(services["api"]) != {"edge", "private"}:
        fail("api must join exactly the edge and private networks")
    for name in ("db", "migrate", "storage-init", "worker"):
        if network_names(services[name]) != {"private"}:
            fail(f"{name} must join only the private network")

    print("PASS: rendered Compose policy is satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
