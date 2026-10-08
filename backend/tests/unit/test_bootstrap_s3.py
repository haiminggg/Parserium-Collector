from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from parserium_collector.cli.bootstrap_s3 import (
    BucketBootstrapError,
    _ensure_private_bucket_with_retry,
    ensure_private_bucket,
    main,
)


def client_error(code: str, *, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test double"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "TestOperation",
    )


class S3ClientTestDouble:
    def __init__(
        self,
        *,
        head_results: list[object] | None = None,
        policy_result: object | None = None,
        acl_result: dict[str, Any] | None = None,
    ) -> None:
        self.head_results = list(head_results or [{}])
        self.policy_result = (
            policy_result
            if policy_result is not None
            else client_error("NoSuchBucketPolicy", status=404)
        )
        self.acl_result = acl_result or {
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "owner"},
                    "Permission": "FULL_CONTROL",
                }
            ]
        }
        self.create_calls: list[dict[str, object]] = []
        self.policy_reads: list[str] = []
        self.acl_reads: list[str] = []

    def head_bucket(self, **kwargs: object) -> dict[str, object]:
        result = self.head_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def create_bucket(self, **kwargs: object) -> dict[str, object]:
        self.create_calls.append(kwargs)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_bucket_policy(self, *, Bucket: str) -> dict[str, object]:
        self.policy_reads.append(Bucket)
        if isinstance(self.policy_result, BaseException):
            raise self.policy_result
        assert isinstance(self.policy_result, dict)
        return self.policy_result

    def get_bucket_acl(self, *, Bucket: str) -> dict[str, Any]:
        self.acl_reads.append(Bucket)
        return self.acl_result


def test_creates_an_absent_private_bucket() -> None:
    client = S3ClientTestDouble(head_results=[client_error("NoSuchBucket", status=404)])

    ensure_private_bucket(client, bucket="parserium-hosted-local", region="us-east-1")  # type: ignore[arg-type]

    assert client.create_calls == [{"Bucket": "parserium-hosted-local"}]
    assert client.policy_reads == ["parserium-hosted-local"]
    assert client.acl_reads == ["parserium-hosted-local"]


def test_accepts_an_existing_owned_private_bucket() -> None:
    client = S3ClientTestDouble()

    ensure_private_bucket(client, bucket="parserium-hosted-local", region="us-east-1")  # type: ignore[arg-type]

    assert client.create_calls == []


def test_non_default_region_is_sent_only_when_creating() -> None:
    client = S3ClientTestDouble(head_results=[client_error("404", status=404)])

    ensure_private_bucket(client, bucket="parserium-hosted-local", region="ap-southeast-1")  # type: ignore[arg-type]

    assert client.create_calls == [
        {
            "Bucket": "parserium-hosted-local",
            "CreateBucketConfiguration": {"LocationConstraint": "ap-southeast-1"},
        }
    ]


def test_retries_a_transient_endpoint_failure_then_succeeds() -> None:
    client = S3ClientTestDouble(
        head_results=[EndpointConnectionError(endpoint_url="https://minio:9000"), {}]
    )
    delays: list[float] = []

    _ensure_private_bucket_with_retry(
        client,  # type: ignore[arg-type]
        bucket="parserium-hosted-local",
        region="us-east-1",
        attempts=3,
        delay_seconds=2,
        sleep=delays.append,
    )

    assert delays == [2]
    assert client.policy_reads == ["parserium-hosted-local"]


def test_exhausts_transient_endpoint_failures() -> None:
    client = S3ClientTestDouble(
        head_results=[
            EndpointConnectionError(endpoint_url="https://minio:9000"),
            EndpointConnectionError(endpoint_url="https://minio:9000"),
        ]
    )

    with pytest.raises(BucketBootstrapError, match="unavailable"):
        _ensure_private_bucket_with_retry(
            client,  # type: ignore[arg-type]
            bucket="parserium-hosted-local",
            region="us-east-1",
            attempts=2,
            delay_seconds=0,
            sleep=lambda _: None,
        )


def test_does_not_retry_bucket_access_failure() -> None:
    client = S3ClientTestDouble(head_results=[client_error("AccessDenied", status=403)])
    delays: list[float] = []

    with pytest.raises(BucketBootstrapError, match="access"):
        _ensure_private_bucket_with_retry(
            client,  # type: ignore[arg-type]
            bucket="parserium-hosted-local",
            region="us-east-1",
            attempts=3,
            delay_seconds=2,
            sleep=delays.append,
        )

    assert delays == []


def test_rejects_any_bucket_policy() -> None:
    client = S3ClientTestDouble(policy_result={"Policy": '{"Version":"2012-10-17"}'})

    with pytest.raises(BucketBootstrapError, match="policy"):
        ensure_private_bucket(client, bucket="parserium-hosted-local", region="us-east-1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "uri",
    [
        "http://acs.amazonaws.com/groups/global/AllUsers",
        "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
    ],
)
def test_rejects_public_acl_grants(uri: str) -> None:
    client = S3ClientTestDouble(
        acl_result={
            "Grants": [
                {
                    "Grantee": {"Type": "Group", "URI": uri},
                    "Permission": "READ",
                }
            ]
        }
    )

    with pytest.raises(BucketBootstrapError, match="public ACL"):
        ensure_private_bucket(client, bucket="parserium-hosted-local", region="us-east-1")  # type: ignore[arg-type]


def configure_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    **overrides: str,
) -> tuple[Path, Path, Path]:
    access = tmp_path / "access"
    secret = tmp_path / "secret"
    ca = tmp_path / "ca.pem"
    access.write_text("exact-access", encoding="utf-8")
    secret.write_text("exact-secret", encoding="utf-8")
    ca.write_text("test-ca", encoding="utf-8")
    values = {
        "PARSERIUM_BOOTSTRAP_S3_ENDPOINT": "https://minio:9000",
        "PARSERIUM_BOOTSTRAP_S3_REGION": "us-east-1",
        "PARSERIUM_BOOTSTRAP_S3_BUCKET": "parserium-hosted-local",
        "PARSERIUM_BOOTSTRAP_S3_ATTEMPTS": "3",
        "PARSERIUM_BOOTSTRAP_S3_DELAY_SECONDS": "0",
        "TEST_S3_ACCESS_KEY_FILE": str(access),
        "TEST_S3_SECRET_KEY_FILE": str(secret),
        "TEST_S3_CA_BUNDLE_FILE": str(ca),
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return access, secret, ca


def test_main_reads_exact_secret_files_and_builds_bounded_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    access, secret, ca = configure_environment(monkeypatch, tmp_path)
    client = S3ClientTestDouble()
    captured: dict[str, Any] = {}

    def fake_client(service_name: str, **kwargs: Any) -> S3ClientTestDouble:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return client

    monkeypatch.setattr(boto3, "client", fake_client)

    assert main() == 0

    assert capsys.readouterr().out == "Hosted-local bucket ready.\n"
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://minio:9000"
    assert captured["region_name"] == "us-east-1"
    assert captured["aws_access_key_id"] == access.read_text(encoding="utf-8")
    assert captured["aws_secret_access_key"] == secret.read_text(encoding="utf-8")
    assert captured["verify"] == str(ca)
    config = captured["config"]
    assert config.signature_version == "s3v4"
    assert config.s3 == {"addressing_style": "path"}
    assert config.connect_timeout <= 10
    assert config.read_timeout <= 30


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PARSERIUM_BOOTSTRAP_S3_ENDPOINT", "http://minio:9000"),
        ("PARSERIUM_BOOTSTRAP_S3_ENDPOINT", "https://minio:9000/path"),
        ("PARSERIUM_BOOTSTRAP_S3_BUCKET", "Unsafe_Bucket"),
        ("PARSERIUM_BOOTSTRAP_S3_ATTEMPTS", "0"),
    ],
)
def test_main_rejects_unsafe_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    configure_environment(monkeypatch, tmp_path, **{name: value})
    called = False

    def unexpected_client(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(boto3, "client", unexpected_client)

    with pytest.raises(BucketBootstrapError, match="configuration"):
        main()

    assert called is False
