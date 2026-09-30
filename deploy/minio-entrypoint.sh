#!/usr/bin/env sh
set -eu
umask 077

read_secret() {
  secret_path=$1
  secret_label=$2
  if [ ! -f "$secret_path" ] || [ ! -r "$secret_path" ]; then
    echo "MinIO $secret_label file is unavailable." >&2
    exit 78
  fi
  secret_value=$(cat "$secret_path")
  if [ -z "$secret_value" ]; then
    echo "MinIO $secret_label file is empty." >&2
    exit 78
  fi
  printf '%s' "$secret_value"
}

MINIO_ROOT_USER=$(read_secret /run/secrets/storage_access_key 'access credential')
MINIO_ROOT_PASSWORD=$(read_secret /run/secrets/storage_secret_key 'secret credential')
export MINIO_ROOT_USER MINIO_ROOT_PASSWORD

if [ "$(id -u)" -eq 0 ]; then
  install -m 0444 /run/secrets/minio_tls_certificate /minio-certs/public.crt
  install -m 0400 /run/secrets/minio_tls_private_key /minio-certs/private.key
  chown 10002:10002 /data /minio-certs/public.crt /minio-certs/private.key
  exec setpriv \
    --reuid=10002 \
    --regid=10002 \
    --clear-groups \
    --inh-caps=-all \
    --ambient-caps=-all \
    --bounding-set=-all \
    --no-new-privs \
    -- /usr/local/bin/minio server /data \
      --certs-dir /minio-certs \
      --address :9000
fi

exec /usr/local/bin/minio server /data \
  --certs-dir /minio-certs \
  --address :9000
