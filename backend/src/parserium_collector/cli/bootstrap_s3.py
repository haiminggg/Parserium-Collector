from __future__ import annotations

import ipaddress
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

MISSING_BUCKET_CODES = frozenset({"404", "NoSuchBucket", "NotFound"})
MISSING_POLICY_CODES = frozenset({"NoSuchBucketPolicy", "NoSuchPolicy"})
PUBLIC_GRANTEE_SUFFIXES = ("/AllUsers", "/AuthenticatedUsers")
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


class BucketBootstrapError(RuntimeError):
    """Raised when the hosted-local bucket cannot be proven private and ready."""


@dataclass(frozen=True)
class BootstrapSettings:
    endpoint: str
    region: str
    bucket: str
    attempts: int
    delay_seconds: float
    access_key: str
    secret_key: str
    ca_bundle: Path


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _http_status(error: ClientError) -> int | None:
    value = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return value if isinstance(value, int) else None


def _create_bucket(client: S3Client, *, bucket: str, region: str) -> None:
    request: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        request["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**request)


def ensure_private_bucket(client: S3Client, *, bucket: str, region: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if _error_code(error) not in MISSING_BUCKET_CODES and _http_status(error) != 404:
            raise
        _create_bucket(client, bucket=bucket, region=region)

    try:
        client.get_bucket_policy(Bucket=bucket)
    except ClientError as error:
        if _error_code(error) not in MISSING_POLICY_CODES:
            raise
    else:
        raise BucketBootstrapError("Hosted-local bucket has an unexpected bucket policy.")

    response = client.get_bucket_acl(Bucket=bucket)
    grants = response.get("Grants")
    if not isinstance(grants, list):
        raise BucketBootstrapError("Hosted-local bucket ACL response is invalid.")
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise BucketBootstrapError("Hosted-local bucket ACL response is invalid.")
        grantee = grant.get("Grantee")
        if not isinstance(grantee, Mapping):
            raise BucketBootstrapError("Hosted-local bucket ACL response is invalid.")
        uri = grantee.get("URI")
        if isinstance(uri, str) and uri.endswith(PUBLIC_GRANTEE_SUFFIXES):
            raise BucketBootstrapError("Hosted-local bucket has a public ACL grant.")


def _is_retryable(error: BaseException) -> bool:
    if isinstance(
        error,
        (
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ),
    ):
        return True
    return isinstance(error, ClientError) and (_http_status(error) or 0) >= 500


def _ensure_private_bucket_with_retry(
    client: S3Client,
    *,
    bucket: str,
    region: str,
    attempts: int,
    delay_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            ensure_private_bucket(client, bucket=bucket, region=region)
            return
        except BucketBootstrapError:
            raise
        except Exception as error:
            if _is_retryable(error):
                if attempt < attempts:
                    sleep(delay_seconds)
                    continue
                raise BucketBootstrapError(
                    "Hosted-local S3 endpoint remained unavailable."
                ) from error
            if isinstance(error, ClientError) and (
                _http_status(error) in {401, 403}
                or _error_code(error)
                in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}
            ):
                raise BucketBootstrapError("Hosted-local bucket access was denied.") from error
            if isinstance(error, (BotoCoreError, ClientError)):
                raise BucketBootstrapError("Hosted-local bucket operation failed.") from error
            raise
    raise BucketBootstrapError("Hosted-local S3 endpoint remained unavailable.")


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip() or value != value.strip() or "\x00" in value:
        raise BucketBootstrapError("Hosted-local bucket configuration is invalid.")
    return value


def _read_secret_file(environment_name: str) -> str:
    raw_path = _required_environment(environment_name)
    path = Path(raw_path)
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BucketBootstrapError("Hosted-local bucket credential file is unreadable.") from error
    value = value.removesuffix("\n").removesuffix("\r")
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise BucketBootstrapError("Hosted-local bucket credential file is invalid.")
    return value


def _valid_bucket_name(value: str) -> bool:
    if BUCKET_PATTERN.fullmatch(value) is None:
        return False
    if ".." in value or ".-" in value or "-." in value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return True
    return False


def _settings_from_environment() -> BootstrapSettings:
    try:
        endpoint = _required_environment("PARSERIUM_BOOTSTRAP_S3_ENDPOINT")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        endpoint = endpoint.rstrip("/")

        region = _required_environment("PARSERIUM_BOOTSTRAP_S3_REGION")
        if len(region) < 3 or REGION_PATTERN.fullmatch(region) is None:
            raise ValueError
        bucket = _required_environment("PARSERIUM_BOOTSTRAP_S3_BUCKET")
        if not _valid_bucket_name(bucket):
            raise ValueError

        attempts = int(os.environ.get("PARSERIUM_BOOTSTRAP_S3_ATTEMPTS", "30"))
        delay_seconds = float(os.environ.get("PARSERIUM_BOOTSTRAP_S3_DELAY_SECONDS", "2"))
        if not 1 <= attempts <= 60 or not 0 <= delay_seconds <= 60:
            raise ValueError

        access_key = _read_secret_file("TEST_S3_ACCESS_KEY_FILE")
        secret_key = _read_secret_file("TEST_S3_SECRET_KEY_FILE")
        ca_bundle = Path(_required_environment("TEST_S3_CA_BUNDLE_FILE"))
        if not ca_bundle.is_file() or ca_bundle.is_symlink():
            raise ValueError
    except (TypeError, ValueError) as error:
        raise BucketBootstrapError("Hosted-local bucket configuration is invalid.") from error
    return BootstrapSettings(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        attempts=attempts,
        delay_seconds=delay_seconds,
        access_key=access_key,
        secret_key=secret_key,
        ca_bundle=ca_bundle,
    )


def main() -> int:
    settings = _settings_from_environment()
    try:
        client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            region_name=settings.region,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            verify=str(settings.ca_bundle),
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=15,
                retries={"total_max_attempts": 1, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )
    except (BotoCoreError, ValueError) as error:
        raise BucketBootstrapError("Hosted-local S3 client configuration failed.") from error
    _ensure_private_bucket_with_retry(
        client,
        bucket=settings.bucket,
        region=settings.region,
        attempts=settings.attempts,
        delay_seconds=settings.delay_seconds,
    )
    print("Hosted-local bucket ready.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BucketBootstrapError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
