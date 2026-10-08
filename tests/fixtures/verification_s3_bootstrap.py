import os
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

WAIT_SECONDS = 90


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required bootstrap setting {name} is missing.")
    return value


def read_secret(path_name: str) -> str:
    path = Path(required_environment(path_name))
    if not path.is_file():
        raise ValueError(f"Bootstrap secret file for {path_name} is missing.")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Bootstrap secret file for {path_name} is empty.")
    return value


def wait_until_ready(client: object) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            client.list_buckets()
            return
        except (BotoCoreError, ClientError):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "TLS MinIO did not become ready before the timeout."
                ) from None
            time.sleep(1)


def ensure_private_bucket(client: object, bucket: str, region: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(error.response.get("Error", {}).get("Code", ""))
        if status != 404 and code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    arguments: dict[str, object] = {"Bucket": bucket}
    if region != "us-east-1":
        arguments["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**arguments)


def main() -> int:
    endpoint = required_environment("TEST_S3_ENDPOINT")
    region = required_environment("TEST_S3_REGION")
    primary_bucket = required_environment("TEST_S3_BUCKET")
    backup_bucket = required_environment("TEST_S3_BACKUP_BUCKET")
    if primary_bucket == backup_bucket:
        raise ValueError("Primary and backup buckets must be distinct.")
    ca_bundle = Path(required_environment("TEST_S3_CA_BUNDLE_FILE"))
    if not ca_bundle.is_file():
        raise ValueError("Bootstrap CA bundle is missing.")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=read_secret("TEST_S3_ACCESS_KEY_FILE"),
        aws_secret_access_key=read_secret("TEST_S3_SECRET_KEY_FILE"),
        verify=str(ca_bundle),
        config=Config(
            signature_version="s3v4",
            connect_timeout=2,
            read_timeout=5,
            retries={"total_max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )
    wait_until_ready(client)
    ensure_private_bucket(client, primary_bucket, region)
    ensure_private_bucket(client, backup_bucket, region)
    print("PASS: private verification buckets are ready over TLS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
