#!/usr/bin/env sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SECRET_DIR="$REPOSITORY/.local/secrets"
mkdir -p "$SECRET_DIR"
SECRET_PATH="$SECRET_DIR/db_password"
if [ ! -f "$SECRET_PATH" ]; then
  dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -d '\n=' | tr '+/' '-_' > "$SECRET_PATH"
fi
chmod 600 "$SECRET_PATH"
LENGTH=$(wc -c < "$SECRET_PATH" | tr -d ' ')
if [ "$LENGTH" -ne 64 ]; then
  echo "Database secret has an invalid encoded length." >&2
  exit 1
fi
echo "PASS: local database secret exists with mode 600."
