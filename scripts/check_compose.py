import argparse
import json
import re
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"Compose policy failed: {message}")


def network_names(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", {})
    if not isinstance(networks, dict):
        fail("service networks must use rendered object form")
    return set(networks)


def volume_at(service: dict[str, Any], target: str) -> dict[str, Any] | None:
    for volume in service.get("volumes", []) or []:
        if isinstance(volume, dict) and volume.get("target") == target:
            return volume
    return None


def secret_sources(service: dict[str, Any]) -> set[str]:
    secrets = service.get("secrets", []) or []
    if not isinstance(secrets, list):
        fail("service secrets must use rendered list form")
    sources: set[str] = set()
    for secret in secrets:
        if not isinstance(secret, dict) or not isinstance(secret.get("source"), str):
            fail("service secret must have a rendered source")
        sources.add(secret["source"])
    return sources


def environment_of(service: dict[str, Any], name: str) -> dict[str, Any]:
    environment = service.get("environment", {})
    if not isinstance(environment, dict):
        fail(f"{name} environment must use rendered object form")
    return environment


def validate_exact_private_allowlist(environment: dict[str, Any], name: str) -> None:
    raw = environment.get("DASHBOARD_FIRECRAWL_REMOTE_PRIVATE_ALLOWLIST")
    if raw is None:
        return
    try:
        entries = json.loads(str(raw))
    except json.JSONDecodeError:
        fail(f"{name} private allowlist must contain exact hostnames only")
    if not isinstance(entries, list):
        fail(f"{name} private allowlist must contain exact hostnames only")
    hostname_pattern = re.compile(
        r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    )
    for entry in entries:
        if not isinstance(entry, str) or entry.count(":") != 1:
            fail(f"{name} private allowlist must contain exact hostnames only")
        hostname, raw_port = entry.rsplit(":", 1)
        if (
            hostname_pattern.fullmatch(hostname) is None
            or not raw_port.isdigit()
            or not 1 <= int(raw_port) <= 65_535
        ):
            fail(f"{name} private allowlist must contain exact hostnames only")


def validate_hosted_firecrawl_policy(
    document: dict[str, Any],
    *,
    api_name: str,
    worker_name: str,
    key_id: str,
) -> None:
    services = document.get("services", {})
    secrets = document.get("secrets", {})
    if not isinstance(services, dict) or not isinstance(secrets, dict):
        fail(f"{api_name} credential wrapping key secret is missing")
    if not isinstance(secrets.get("firecrawl_credential_wrapping_key"), dict):
        fail(f"{api_name} credential wrapping key secret is missing")

    credential_consumers = {api_name, worker_name}
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        mounted = "firecrawl_credential_wrapping_key" in secret_sources(service)
        if service_name in credential_consumers:
            if not mounted:
                fail(f"{service_name} must mount the credential wrapping key")
        elif mounted:
            fail("credential wrapping key must be mounted only by API and worker")

    api_environment = environment_of(services[api_name], api_name)
    worker_environment = environment_of(services[worker_name], worker_name)
    for service_name, environment in (
        (api_name, api_environment),
        (worker_name, worker_environment),
    ):
        if (
            environment.get("DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_FILE")
            != "/run/secrets/firecrawl_credential_wrapping_key"
            or environment.get("DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_ID") != key_id
        ):
            fail(f"{service_name} must configure the credential wrapping key exactly")
    for name, environment in (
        (api_name, api_environment),
        (worker_name, worker_environment),
    ):
        if "DASHBOARD_FIRECRAWL_BASE_URL" in environment:
            fail(f"{name} must not configure a global Firecrawl endpoint")
    validate_exact_private_allowlist(api_environment, api_name)
    if "DASHBOARD_FIRECRAWL_REMOTE_PRIVATE_ALLOWLIST" in worker_environment:
        fail(f"{worker_name} must not configure a remote Firecrawl private allowlist")


def has_tmpfs_target(service: dict[str, Any], target: str) -> bool:
    for entry in service.get("tmpfs", []) or []:
        if isinstance(entry, str) and entry.split(":", 1)[0] == target:
            return True
        if isinstance(entry, dict) and entry.get("target") == target:
            return True
    return False


def validate_verification(document: dict[str, Any]) -> None:
    services = document.get("services", {})
    required_names = {
        "hosted-api",
        "hosted-worker",
        "minio",
        "storage-bootstrap",
        "test-firecrawl",
        "test-firecrawl-self-hosted",
        "verification-oidc",
    }
    if not isinstance(services, dict) or not required_names.issubset(services):
        actual = sorted(services) if isinstance(services, dict) else []
        fail(
            "verification topology is missing required services "
            f"{sorted(required_names - set(actual))}"
        )

    validate_hosted_firecrawl_policy(
        document,
        api_name="hosted-api",
        worker_name="hosted-worker",
        key_id="verification-v1",
    )

    hosted_api_environment = environment_of(services["hosted-api"], "hosted-api")
    if hosted_api_environment.get("DASHBOARD_FIRECRAWL_REMOTE_ALLOWED_PORTS") != "[9444]":
        fail("hosted-api must allow only the verification Firecrawl TLS port")
    if (
        hosted_api_environment.get("DASHBOARD_FIRECRAWL_REMOTE_PRIVATE_ALLOWLIST")
        != '["test-firecrawl:9444"]'
    ):
        fail("hosted-api must allow only the exact verification Firecrawl hostname")
    if hosted_api_environment.get("SSL_CERT_FILE") != "/run/secrets/storage_ca_bundle":
        fail("hosted-api must trust only the generated verification CA bundle")

    firecrawl = services["test-firecrawl"]
    if not isinstance(firecrawl, dict):
        fail("test-firecrawl is not a rendered service object")
    if firecrawl.get("ports"):
        fail("test-firecrawl must not publish a host port")
    if network_names(firecrawl) != {"edge"}:
        fail("test-firecrawl must join only the edge network")
    if firecrawl.get("command") != [
        "python",
        "/verification/verification_firecrawl_stub.py",
    ]:
        fail("test-firecrawl must use the authenticated TLS fixture mode")
    if firecrawl.get("user") != "0:0" or firecrawl.get("entrypoint") != [
        "/usr/local/bin/parserium-entrypoint"
    ]:
        fail("test-firecrawl must use the audited secret handoff entrypoint")
    if secret_sources(firecrawl) != {
        "firecrawl_test_bearer",
        "storage_ca_bundle",
        "test_firecrawl_tls_certificate",
        "test_firecrawl_tls_private_key",
    }:
        fail("test-firecrawl must mount only generated bearer and TLS material")
    if firecrawl.get("read_only") is not True:
        fail("test-firecrawl root filesystem must be read-only")
    if "ALL" not in (firecrawl.get("cap_drop") or []):
        fail("test-firecrawl must drop all capabilities")
    if set(firecrawl.get("cap_add") or []) != {
        "CHOWN",
        "DAC_READ_SEARCH",
        "SETGID",
        "SETPCAP",
        "SETUID",
    }:
        fail("test-firecrawl must receive only the secret handoff capabilities")
    if "no-new-privileges:true" not in (firecrawl.get("security_opt") or []):
        fail("test-firecrawl must set no-new-privileges")

    legacy_firecrawl = services["test-firecrawl-self-hosted"]
    if not isinstance(legacy_firecrawl, dict):
        fail("test-firecrawl-self-hosted is not a rendered service object")
    if legacy_firecrawl.get("command") != [
        "python",
        "/verification/verification_firecrawl_stub.py",
        "--legacy-http",
    ]:
        fail("self-hosted verification must opt into the legacy HTTP fixture")
    if secret_sources(legacy_firecrawl):
        fail("the legacy self-hosted fixture must not receive hosted credentials")

    minio = services["minio"]
    if not isinstance(minio, dict):
        fail("minio is not a rendered service object")
    if minio.get("ports"):
        fail("minio must not publish a host port")
    if network_names(minio) != {"private"}:
        fail("minio must join only the private network")
    build = minio.get("build", {})
    if not isinstance(build, dict) or build.get("target") != "minio-verification":
        fail("minio must use the verification-only source build target")
    if minio.get("user") != "0:0":
        fail("minio must start its audited secret handoff as root")
    if minio.get("entrypoint") != ["/usr/local/bin/minio-entrypoint"]:
        fail("minio must use the audited nonroot handoff entrypoint")
    if minio.get("read_only") is not True:
        fail("minio root filesystem must be read-only")
    if "ALL" not in (minio.get("cap_drop") or []):
        fail("minio must drop all capabilities")
    if set(minio.get("cap_add") or []) != {
        "CHOWN",
        "DAC_OVERRIDE",
        "SETGID",
        "SETPCAP",
        "SETUID",
    }:
        fail("minio must receive only the nonroot handoff capabilities")
    if "no-new-privileges:true" not in (minio.get("security_opt") or []):
        fail("minio must set no-new-privileges")
    if "--console-address" in json.dumps(
        [minio.get("entrypoint"), minio.get("command")], sort_keys=True
    ):
        fail("minio must not configure an administrative console address")
    if secret_sources(minio) != {
        "storage_access_key",
        "storage_secret_key",
        "minio_tls_certificate",
        "minio_tls_private_key",
    }:
        fail("minio must mount only generated credentials and TLS material as secrets")
    minio_environment = environment_of(minio, "minio")
    if any(name.startswith("MINIO_ROOT_") for name in minio_environment):
        fail("minio root credentials must not appear in the environment")
    data_mount = volume_at(minio, "/data")
    if data_mount is None or data_mount.get("type") != "volume":
        fail("minio data must use an isolated verification volume")
    if not has_tmpfs_target(minio, "/minio-certs"):
        fail("minio runtime certificates must use a dedicated tmpfs")

    bootstrap = services["storage-bootstrap"]
    if not isinstance(bootstrap, dict):
        fail("storage-bootstrap is not a rendered service object")
    if bootstrap.get("ports"):
        fail("storage-bootstrap must not publish a host port")
    if network_names(bootstrap) != {"private"}:
        fail("storage-bootstrap must join only the private network")
    if bootstrap.get("user") != "0:0" or bootstrap.get("entrypoint") != [
        "/usr/local/bin/parserium-entrypoint"
    ]:
        fail("storage-bootstrap must use the audited secret handoff entrypoint")
    if secret_sources(bootstrap) != {
        "storage_access_key",
        "storage_secret_key",
        "storage_ca_bundle",
    }:
        fail("storage-bootstrap must mount only generated S3 credentials and CA")
    bootstrap_environment = environment_of(bootstrap, "storage-bootstrap")
    if bootstrap_environment.get("TEST_S3_ENDPOINT") != "https://minio:9000":
        fail("storage-bootstrap must use the private TLS MinIO endpoint")
    if bootstrap_environment.get("TEST_S3_REGION") != "us-east-1":
        fail("storage-bootstrap must use the verification S3 region")
    primary_bucket = bootstrap_environment.get("TEST_S3_BUCKET")
    backup_bucket = bootstrap_environment.get("TEST_S3_BACKUP_BUCKET")
    if (
        not isinstance(primary_bucket, str)
        or not primary_bucket
        or not isinstance(backup_bucket, str)
        or not backup_bucket
        or primary_bucket == backup_bucket
    ):
        fail("storage-bootstrap must configure distinct primary and backup buckets")

    expected_storage = {
        "DASHBOARD_DEPLOYMENT_MODE": "hosted",
        "DASHBOARD_EXPECTED_MIGRATION": "0009_unified_activity_history",
        "DASHBOARD_STORAGE_BACKEND": "s3",
        "DASHBOARD_STORAGE_ENDPOINT": "https://minio:9000",
        "DASHBOARD_STORAGE_REGION": "us-east-1",
        "DASHBOARD_STORAGE_BUCKET": primary_bucket,
        "DASHBOARD_STORAGE_FORCE_PATH_STYLE": "true",
        "DASHBOARD_STORAGE_ACCESS_KEY_FILE": "/run/secrets/storage_access_key",
        "DASHBOARD_STORAGE_SECRET_KEY_FILE": "/run/secrets/storage_secret_key",
        "DASHBOARD_STORAGE_CA_BUNDLE_FILE": "/run/secrets/storage_ca_bundle",
        "DASHBOARD_SCRATCH_ROOT": "/var/lib/parserium-collector-scratch",
    }
    for name in ("hosted-api", "hosted-worker"):
        service = services[name]
        if not isinstance(service, dict):
            fail(f"{name} is not a rendered service object")
        environment = environment_of(service, name)
        if "DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT" in environment:
            fail(
                f"{name} shared verification environment must leave the signed URL "
                "endpoint unset"
            )
        if environment.get("DASHBOARD_OIDC_PROVIDER_LABEL") != "Google":
            fail(f"{name} must render the Google provider label")
        for setting, expected in expected_storage.items():
            if str(environment.get(setting, "")).lower() != str(expected).lower():
                fail(f"{name} must configure {setting} for private TLS S3 storage")
        if not {
            "storage_access_key",
            "storage_secret_key",
            "storage_ca_bundle",
        }.issubset(secret_sources(service)):
            fail(f"{name} must mount generated S3 credentials and CA")
        if volume_at(service, "/var/lib/parserium-collector") is not None:
            fail(f"{name} must not mount filesystem durable storage")
        if volume_at(service, "/exports") is not None:
            fail(f"{name} must not mount the self-hosted export directory")
        if not has_tmpfs_target(service, "/var/lib/parserium-collector-scratch"):
            fail(f"{name} scratch storage must use a dedicated tmpfs")
        dependencies = service.get("depends_on", {})
        if not isinstance(dependencies, dict) or dependencies.get("storage-bootstrap", {}).get(
            "condition"
        ) != "service_completed_successfully":
            fail(f"{name} must wait for private bucket bootstrap")

    secrets = document.get("secrets", {})
    if not isinstance(secrets, dict):
        fail("verification secrets must use rendered object form")
    generated_secret_files = {
        "firecrawl_test_bearer": "firecrawl-test-bearer",
        "storage_access_key": "minio-access-key",
        "storage_secret_key": "minio-secret-key",
        "storage_ca_bundle": "ca.pem",
        "minio_tls_certificate": "minio-cert.pem",
        "minio_tls_private_key": "minio-key.pem",
        "firecrawl_credential_wrapping_key": "firecrawl-credential-wrapping-key",
        "discovery_fingerprint_secret": "discovery-fingerprint-secret",
        "test_firecrawl_tls_certificate": "test-firecrawl-cert.pem",
        "test_firecrawl_tls_private_key": "test-firecrawl-key.pem",
    }
    for name, filename in generated_secret_files.items():
        secret = secrets.get(name)
        if not isinstance(secret, dict):
            fail(f"verification secret {name} is missing")
        source = secret.get("file")
        if not isinstance(source, str):
            fail(f"verification secret {name} has no generated file")
        normalized = normalized_host_path(source)
        if not normalized.endswith(f"/.local/verification-pki/{filename}"):
            fail(f"verification secret {name} must come from ignored generated storage")


def configured_value(path: Path, name: str) -> str:
    matches = [
        line.split("=", 1)[1]
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith(f"{name}=")
    ]
    if len(matches) != 1:
        fail(f"local configuration must contain exactly one {name} entry")
    return matches[0]


def normalized_host_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/")


def is_filesystem_root(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized == "/" or re.fullmatch(r"[A-Za-z]:/?", normalized) is not None


def validate_hosted_local(document: dict[str, Any], state_root: Path) -> None:
    if document.get("name") != "parserium-hosted-local":
        fail("hosted-local project name must be parserium-hosted-local")
    resolved_state_root = state_root.resolve()
    if not resolved_state_root.is_dir() or is_filesystem_root(str(resolved_state_root)):
        fail("hosted-local state root must be an existing bounded directory")

    services = document.get("services", {})
    required_services = {"api", "db", "migrate", "minio", "storage-bootstrap", "worker"}
    if not isinstance(services, dict) or set(services) != required_services:
        actual = sorted(services) if isinstance(services, dict) else []
        fail(f"hosted-local expected services {sorted(required_services)}, got {actual}")
    for name, service in services.items():
        if not isinstance(service, dict):
            fail(f"hosted-local {name} is not a rendered service object")

    validate_hosted_firecrawl_policy(
        document,
        api_name="api",
        worker_name="worker",
        key_id="hosted-local-v1",
    )

    expected_postgres = (
        "docker.io/library/postgres:18.6-alpine3.24@sha256:"
        "d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2"
    )
    if services["db"].get("image") != expected_postgres:
        fail("hosted-local db must use the immutable audited PostgreSQL image")
    for name in ("api", "migrate", "storage-bootstrap", "worker"):
        service = services[name]
        if service.get("image") != "parserium-collector:hosted-local":
            fail(f"{name} must use the hosted-local image tag")
        build = service.get("build", {})
        if not isinstance(build, dict) or build.get("target") != "runtime":
            fail(f"{name} must use the hosted-local runtime build target")
    minio = services["minio"]
    if minio.get("image") != "parserium-minio:hosted-local":
        fail("minio must use the hosted-local image tag")
    minio_build = minio.get("build", {})
    if not isinstance(minio_build, dict) or minio_build.get("target") != "minio-hosted-local":
        fail("minio must use the hosted-local build target")

    published: list[tuple[str, dict[str, Any]]] = []
    for name, service in services.items():
        for port in service.get("ports", []) or []:
            if not isinstance(port, dict):
                fail(f"hosted-local {name} has an invalid rendered port")
            published.append((name, port))
    if services["db"].get("ports"):
        fail("hosted-local db must not publish a port")
    if len(published) != 2 or {name for name, _ in published} != {"api", "minio"}:
        fail("hosted-local must publish only api and minio loopback ports")
    expected_ports = {"api": ("8443", "8443"), "minio": ("9000", "9000")}
    for name, port in published:
        if port.get("host_ip") != "127.0.0.1":
            fail(f"hosted-local {name} port must bind only to loopback")
        expected_published, expected_target = expected_ports[name]
        if (
            str(port.get("published")) != expected_published
            or str(port.get("target")) != expected_target
        ):
            fail(f"hosted-local {name} port mapping is invalid")

    networks = document.get("networks", {})
    if not isinstance(networks, dict) or set(networks) != {"edge", "private"}:
        fail("hosted-local must define exactly edge and private networks")
    private = networks.get("private", {})
    if not isinstance(private, dict) or private.get("internal") is not True:
        fail("hosted-local private network must be internal")
    for name in ("db", "migrate", "minio", "storage-bootstrap"):
        if network_names(services[name]) != {"private"}:
            if name == "minio":
                fail("hosted-local minio must join only private")
            fail(f"hosted-local {name} must join only private")
    for name in ("api", "worker"):
        if network_names(services[name]) != {"edge", "private"}:
            fail(f"hosted-local {name} must join exactly edge and private")

    volumes = document.get("volumes", {})
    expected_volume_names = {
        "database": "parserium-hosted-local-database",
        "minio-data": "parserium-hosted-local-minio-data",
    }
    if not isinstance(volumes, dict) or set(volumes) != set(expected_volume_names):
        fail("hosted-local must define only project-specific volumes")
    for key, expected_name in expected_volume_names.items():
        volume = volumes.get(key)
        if not isinstance(volume, dict) or volume.get("name") != expected_name:
            fail("hosted-local volumes must use project-specific volume names")
    database_mount = volume_at(services["db"], "/var/lib/postgresql")
    if database_mount is None or database_mount.get("source") != "database":
        fail("hosted-local db must use its project-specific volume")
    minio_mount = volume_at(minio, "/data")
    if minio_mount is None or minio_mount.get("source") != "minio-data":
        fail("hosted-local minio must use its project-specific volume")
    for name in ("api", "migrate", "storage-bootstrap", "worker"):
        for volume in services[name].get("volumes", []) or []:
            if isinstance(volume, dict) and volume.get("target") in {
                "/exports",
                "/var/lib/parserium-collector",
            }:
                fail(f"hosted-local {name} must not mount self-hosted storage")

    secrets = document.get("secrets", {})
    expected_secret_files = {
        "db_password": "secrets/db_password",
        "session_signing_secret": "secrets/session_signing_secret",
        "oidc_client_secret": "secrets/google_client_secret",
        "storage_access_key": "secrets/minio_access_key",
        "storage_secret_key": "secrets/minio_secret_key",
        "storage_ca_bundle": "pki/ca.pem",
        "tls_certificate": "pki/parserium-cert.pem",
        "tls_private_key": "pki/parserium-key.pem",
        "minio_tls_certificate": "pki/minio-cert.pem",
        "minio_tls_private_key": "pki/minio-key.pem",
        "firecrawl_credential_wrapping_key": "secrets/firecrawl_credential_wrapping_key",
        "discovery_fingerprint_secret": "secrets/discovery_fingerprint_secret",
    }
    if not isinstance(secrets, dict) or set(secrets) != set(expected_secret_files):
        fail("hosted-local must define the exact required secrets")
    serialized = json.dumps(document, sort_keys=True)
    for name, relative_path in expected_secret_files.items():
        secret = secrets.get(name)
        if not isinstance(secret, dict) or not isinstance(secret.get("file"), str):
            fail(f"hosted-local secret {name} is missing its file source")
        source = Path(secret["file"]).resolve()
        expected_source = (resolved_state_root / Path(relative_path)).resolve()
        if source != expected_source:
            fail(f"hosted-local secret {name} must remain below the validated state root")
        try:
            value = source.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            fail(f"hosted-local secret {name} is unreadable: {type(error).__name__}")
        if value and value in serialized:
            fail(f"hosted-local rendered Compose contains a raw secret value from {name}")

    required_service_secrets = {
        "db": {"db_password"},
        "minio": {
            "storage_access_key",
            "storage_secret_key",
            "minio_tls_certificate",
            "minio_tls_private_key",
        },
        "storage-bootstrap": {
            "storage_access_key",
            "storage_secret_key",
            "storage_ca_bundle",
        },
        "migrate": {"db_password"},
        "worker": {
            "db_password",
            "oidc_client_secret",
            "storage_access_key",
            "storage_secret_key",
            "storage_ca_bundle",
            "firecrawl_credential_wrapping_key",
            "discovery_fingerprint_secret",
        },
        "api": {
            "db_password",
            "session_signing_secret",
            "oidc_client_secret",
            "storage_access_key",
            "storage_secret_key",
            "storage_ca_bundle",
            "tls_certificate",
            "tls_private_key",
            "firecrawl_credential_wrapping_key",
            "discovery_fingerprint_secret",
        },
    }
    for name, expected in required_service_secrets.items():
        if secret_sources(services[name]) != expected:
            fail(f"hosted-local {name} must mount the exact required secrets")

    for name, service in services.items():
        environment = environment_of(service, f"hosted-local {name}")
        for setting, value in environment.items():
            upper = setting.upper()
            if any(
                token in upper for token in ("PASSWORD", "SECRET", "ACCESS_KEY")
            ) and not upper.endswith("_FILE"):
                fail(f"hosted-local {name} contains a raw secret environment setting")

    expected_hosted_environment = {
        "DASHBOARD_DEPLOYMENT_MODE": "hosted",
        "DASHBOARD_PUBLIC_ORIGIN": "https://localhost:8443",
        "DASHBOARD_SESSION_COOKIE_SECURE": "true",
        "DASHBOARD_STORAGE_BACKEND": "s3",
        "DASHBOARD_STORAGE_ENDPOINT": "https://minio:9000",
        "DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT": "https://localhost:9000",
        "DASHBOARD_STORAGE_REGION": "us-east-1",
        "DASHBOARD_STORAGE_BUCKET": "parserium-hosted-local",
        "DASHBOARD_STORAGE_FORCE_PATH_STYLE": "true",
        "DASHBOARD_OIDC_ISSUER": "https://accounts.google.com",
        "DASHBOARD_OIDC_PROVIDER_LABEL": "Google",
        "DASHBOARD_OIDC_REDIRECT_URI": "https://localhost:8443/api/v1/auth/callback",
    }
    for name in ("api", "worker"):
        environment = environment_of(services[name], f"hosted-local {name}")
        for setting, expected in expected_hosted_environment.items():
            if str(environment.get(setting, "")).lower() != expected.lower():
                if setting == "DASHBOARD_STORAGE_ENDPOINT":
                    fail(f"hosted-local {name} must use the private TLS S3 endpoint")
                if setting == "DASHBOARD_STORAGE_BACKEND":
                    fail(f"hosted-local {name} must use S3 durable storage")
                if setting == "DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT":
                    fail(f"hosted-local {name} must use the exact signed URL endpoint")
                fail(f"hosted-local {name} must configure {setting} exactly")
        if (
            volume_at(services[name], "/exports") is not None
            or volume_at(services[name], "/var/lib/parserium-collector") is not None
        ):
            fail(f"hosted-local {name} must not mount filesystem durable storage")
        if not has_tmpfs_target(services[name], "/var/lib/parserium-collector-scratch"):
            fail(f"hosted-local {name} must use scratch tmpfs")

    bootstrap_environment = environment_of(services["storage-bootstrap"], "storage-bootstrap")
    expected_bootstrap = {
        "PARSERIUM_BOOTSTRAP_S3_ENDPOINT": "https://minio:9000",
        "PARSERIUM_BOOTSTRAP_S3_REGION": "us-east-1",
        "PARSERIUM_BOOTSTRAP_S3_BUCKET": "parserium-hosted-local",
        "PARSERIUM_BOOTSTRAP_S3_ATTEMPTS": "30",
        "PARSERIUM_BOOTSTRAP_S3_DELAY_SECONDS": "2",
    }
    if bootstrap_environment != expected_bootstrap:
        fail("hosted-local storage-bootstrap settings must be exact")
    migrate_environment = environment_of(services["migrate"], "migrate")
    expected_migrate = {
        "DASHBOARD_DB_HOST": "db",
        "DASHBOARD_DB_NAME": "parserium_collector",
        "DASHBOARD_DB_USER": "parserium_collector",
        "DASHBOARD_DB_PASSWORD_FILE": "/run/secrets/db_password",
        "DASHBOARD_EXPECTED_MIGRATION": "0009_unified_activity_history",
    }
    if migrate_environment != expected_migrate:
        fail("hosted-local migrate must receive only exact database and migration settings")

    if minio.get("ports") is None or network_names(minio) != {"private"}:
        fail("hosted-local minio must join only private while publishing loopback TLS")
    if minio.get("user") != "0:0" or minio.get("entrypoint") != ["/usr/local/bin/minio-entrypoint"]:
        fail("hosted-local minio must use the audited secret handoff")
    if minio.get("read_only") is not True or not has_tmpfs_target(minio, "/minio-certs"):
        fail("hosted-local minio must use a read-only root and certificate tmpfs")
    if "--console-address" in json.dumps(
        [minio.get("entrypoint"), minio.get("command")], sort_keys=True
    ):
        fail("hosted-local minio must not configure an administrative console")
    if any(name.startswith("MINIO_ROOT_") for name in environment_of(minio, "minio")):
        fail("hosted-local minio root credentials must not appear in the environment")

    expected_entrypoint = ["/usr/local/bin/parserium-entrypoint"]
    expected_caps = {"CHOWN", "DAC_READ_SEARCH", "SETGID", "SETPCAP", "SETUID"}
    for name in ("api", "migrate", "storage-bootstrap", "worker"):
        service = services[name]
        if service.get("user") != "0:0" or service.get("entrypoint") != expected_entrypoint:
            fail(f"hosted-local {name} must use audited nonroot secret handoff")
        if service.get("read_only") is not True:
            fail(f"hosted-local {name} root filesystem must be read-only")
        if set(service.get("cap_add") or []) != expected_caps:
            fail(f"hosted-local {name} secret handoff capabilities are invalid")
        if "ALL" not in (service.get("cap_drop") or []):
            fail(f"hosted-local {name} must drop all capabilities")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            fail(f"hosted-local {name} must set no-new-privileges")

    expected_api_command = [
        "uvicorn",
        "parserium_collector.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8443",
        "--ssl-certfile",
        "/tmp/parserium-secrets/tls_certificate",
        "--ssl-keyfile",
        "/tmp/parserium-secrets/tls_private_key",
        "--no-access-log",
    ]
    if services["api"].get("command") != expected_api_command:
        fail("hosted-local api must use the exact audited TLS command")
    if services["storage-bootstrap"].get("command") != [
        "python",
        "-m",
        "parserium_collector.cli.bootstrap_s3",
    ]:
        fail("hosted-local storage-bootstrap must use the private bucket CLI")

    for name in ("api", "worker"):
        extra_hosts = json.dumps(services[name].get("extra_hosts", []))
        if "host.docker.internal" not in extra_hosts or "host-gateway" not in extra_hosts:
            fail(f"hosted-local {name} must map the Docker host gateway")

    api_dependencies = services["api"].get("depends_on", {})
    worker_dependencies = services["worker"].get("depends_on", {})
    if not isinstance(api_dependencies, dict) or any(
        api_dependencies.get(service, {}).get("condition") != condition
        for service, condition in {
            "migrate": "service_completed_successfully",
            "storage-bootstrap": "service_completed_successfully",
            "worker": "service_started",
        }.items()
    ):
        fail("hosted-local api dependencies are incomplete")
    if not isinstance(worker_dependencies, dict) or any(
        worker_dependencies.get(service, {}).get("condition") != "service_completed_successfully"
        for service in ("migrate", "storage-bootstrap")
    ):
        fail("hosted-local worker dependencies are incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--verification-config", type=Path)
    parser.add_argument("--hosted-local-config", type=Path)
    parser.add_argument("--hosted-local-state-root", type=Path)
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

    secret_handoff_caps = {"CHOWN", "DAC_READ_SEARCH", "SETGID", "SETPCAP", "SETUID"}
    for name in ("api", "migrate", "worker"):
        service = services[name]
        if service.get("user") != "0:0":
            fail(f"{name} must start its audited secret handoff as root")
        if service.get("entrypoint") != ["/usr/local/bin/parserium-entrypoint"]:
            fail(f"{name} must use the audited secret handoff entrypoint")
        if set(service.get("cap_add") or []) != secret_handoff_caps:
            fail(f"{name} must receive only the secret handoff capabilities")

    if services["api"].get("command") != [
        "uvicorn",
        "parserium_collector.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--no-access-log",
    ]:
        fail("api must provide the explicit audited application command")

    storage_init = services["storage-init"]
    if storage_init.get("entrypoint") != ["/usr/local/bin/parserium-entrypoint"]:
        fail("storage-init must use the audited container entrypoint")
    storage_environment = storage_init.get("environment", {})
    if not isinstance(storage_environment, dict) or storage_environment.get(
        "PARSERIUM_KEEP_ROOT"
    ) != "1":
        fail("storage-init must explicitly retain root for volume ownership setup")

    if "no-new-privileges:true" not in (services["db"].get("security_opt") or []):
        fail("db does not set no-new-privileges")

    secrets = document.get("secrets", {})
    if not isinstance(secrets, dict) or set(secrets) != {
        "db_password",
        "session_signing_secret",
        "discovery_fingerprint_secret",
    }:
        fail("exactly the database, session signing, and discovery secrets must exist")
    if secret_sources(services["api"]) != {
        "db_password",
        "session_signing_secret",
        "discovery_fingerprint_secret",
    }:
        fail("api must mount the database, session signing, and discovery secrets")
    if secret_sources(services["worker"]) != {
        "db_password",
        "discovery_fingerprint_secret",
    }:
        fail("worker must mount the database and discovery secrets")

    api_environment = services["api"].get("environment", {})
    if not isinstance(api_environment, dict) or api_environment.get(
        "DASHBOARD_FIRECRAWL_BASE_URL"
    ) != "http://host.docker.internal:3002":
        fail("api must target the local Firecrawl endpoint")
    rendered_extra_hosts = json.dumps(services["api"].get("extra_hosts", []))
    if "host.docker.internal" not in rendered_extra_hosts or "host-gateway" not in rendered_extra_hosts:
        fail("api must map the cross-platform Docker host gateway")

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
    if network_names(services["worker"]) != {"edge", "private"}:
        fail("worker must join exactly the edge and private networks")
    for name in ("db", "migrate", "storage-init"):
        if network_names(services[name]) != {"private"}:
            fail(f"{name} must join only the private network")

    configured_export = configured_value(args.local_config, "PARSERIUM_EXPORT_ROOT")
    if is_filesystem_root(configured_export):
        fail("export bind source must not be a filesystem root")
    expected_source = normalized_host_path(configured_export)
    bind_mounts: list[tuple[str, dict[str, Any]]] = []
    for name, service_value in services.items():
        assert isinstance(service_value, dict)
        for volume in service_value.get("volumes", []) or []:
            if not isinstance(volume, dict):
                fail(f"{name} has an invalid rendered volume")
            if volume.get("type") == "bind":
                bind_mounts.append((name, volume))
    if len(bind_mounts) != 2 or {name for name, _ in bind_mounts} != {"api", "worker"}:
        fail("exactly api and worker must receive export bind mounts")
    for name, volume in bind_mounts:
        source = volume.get("source")
        if not isinstance(source, str) or normalized_host_path(source) != expected_source:
            fail(f"{name} export bind does not match local configuration")
        if volume.get("target") != "/exports":
            fail(f"{name} export bind must target /exports")
        if name == "api" and volume.get("read_only") is not True:
            fail("api export bind must be read-only")
        if name == "worker" and volume.get("read_only") is True:
            fail("worker export bind must be writable")

    required_acquisition_settings = {
        "DASHBOARD_EXPORT_ROOT",
        "DASHBOARD_DOWNLOAD_MAX_BYTES",
        "DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS",
        "DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS",
        "DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS",
        "DASHBOARD_DOWNLOAD_MAX_REDIRECTS",
        "DASHBOARD_DOWNLOAD_MAX_ATTEMPTS",
        "DASHBOARD_DOWNLOAD_RETRY_BASE_SECONDS",
        "DASHBOARD_DOWNLOAD_LEASE_SECONDS",
        "DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS",
        "DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST",
        "DASHBOARD_DOCX_MAX_EXPANDED_BYTES",
        "DASHBOARD_DOCX_MAX_EXPANSION_RATIO",
    }
    for name in ("api", "worker"):
        environment = services[name].get("environment", {})
        if not isinstance(environment, dict):
            fail(f"{name} environment must use rendered object form")
        missing = required_acquisition_settings - set(environment)
        if missing:
            fail(f"{name} is missing acquisition settings {sorted(missing)}")
        if environment.get("DASHBOARD_EXPORT_ROOT") != "/exports":
            fail(f"{name} must use /exports as its container export root")
        for setting in (
            "DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS",
            "DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST",
        ):
            try:
                parsed = json.loads(str(environment[setting]))
            except json.JSONDecodeError:
                fail(f"{name} {setting} must contain valid JSON")
            if not isinstance(parsed, list):
                fail(f"{name} {setting} must contain a JSON list")

    for name in ("api", "migrate", "worker"):
        environment = services[name].get("environment", {})
        if not isinstance(environment, dict):
            fail(f"{name} environment must use rendered object form")
        if environment.get("DASHBOARD_DEPLOYMENT_MODE") != "self_hosted":
            fail(f"{name} must explicitly use self_hosted deployment mode")
        if (
            environment.get("DASHBOARD_EXPECTED_MIGRATION")
            != "0009_unified_activity_history"
        ):
            fail(f"{name} must expect migration 0009_unified_activity_history")
        if environment.get("DASHBOARD_STORAGE_BACKEND") != "filesystem":
            fail(f"{name} must explicitly use filesystem durable storage")
        if environment.get("DASHBOARD_STORAGE_ROOT") != "/var/lib/parserium-collector":
            fail(f"{name} must use the dedicated durable storage root")
        if (
            environment.get("DASHBOARD_SCRATCH_ROOT")
            != "/var/lib/parserium-collector-scratch"
        ):
            fail(f"{name} must use the dedicated scratch storage root")

    durable_target = "/var/lib/parserium-collector"
    scratch_target = "/var/lib/parserium-collector-scratch"
    storage_init_durable = volume_at(storage_init, durable_target)
    storage_init_scratch = volume_at(storage_init, scratch_target)
    if storage_init_durable is None or storage_init_scratch is None:
        fail("storage-init must mount both durable and scratch volumes")
    if storage_init_durable.get("source") == storage_init_scratch.get("source"):
        fail("durable and scratch storage must use separate volumes")
    for name in ("api", "worker"):
        durable = volume_at(services[name], durable_target)
        scratch = volume_at(services[name], scratch_target)
        if durable is None or durable.get("source") != storage_init_durable.get("source"):
            fail(f"{name} must mount the initialized durable volume")
        if scratch is None or scratch.get("source") != storage_init_scratch.get("source"):
            fail(f"{name} must mount the initialized scratch volume")

    if args.verification_config is not None:
        verification_document: dict[str, Any] = json.loads(
            args.verification_config.read_text(encoding="utf-8-sig")
        )
        validate_verification(verification_document)

    if (args.hosted_local_config is None) != (args.hosted_local_state_root is None):
        fail("hosted-local config and state root must be supplied together")
    if args.hosted_local_config is not None and args.hosted_local_state_root is not None:
        hosted_local_document: dict[str, Any] = json.loads(
            args.hosted_local_config.read_text(encoding="utf-8-sig")
        )
        validate_hosted_local(hosted_local_document, args.hosted_local_state_root)

    print("PASS: rendered Compose policy is satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
