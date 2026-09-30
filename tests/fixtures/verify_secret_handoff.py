"""Test-only assertion process for the container secret handoff."""

# ruff: noqa: S101

import os
from pathlib import Path

process_status = {
    key: value.strip()
    for key, value in (
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
        if ":" in line
    )
}
assert os.getuid() == 10001
assert os.getgid() == 10001
assert int(process_status["CapEff"], 16) == 0
assert int(process_status["CapBnd"], 16) == 0
expected_secrets = {
    "DASHBOARD_DB_PASSWORD_FILE": (0o400, 64),
    "DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_FILE": (0o400, 44),
    "TEST_FIRECRAWL_BEARER_FILE": (0o400, 64),
    "SSL_CERT_FILE": (0o444, 7),
}
for environment_name, (mode, length) in expected_secrets.items():
    secret_path = Path(os.environ[environment_name])
    metadata = secret_path.stat()
    assert metadata.st_uid == 10001
    assert metadata.st_gid == 10001
    assert metadata.st_mode & 0o777 == mode
    assert len(secret_path.read_bytes()) == length
print("PASS: container secret handoff drops privileges and preserves root-only access.")
