#!/usr/bin/env sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SECRET_DIR="$REPOSITORY/.local/secrets"
mkdir -p "$SECRET_DIR"

initialize_secret() {
  secret_path=$1
  label=$2
  if [ -e "$secret_path" ] || [ -L "$secret_path" ]; then
    if [ ! -f "$secret_path" ] || [ -L "$secret_path" ]; then
      echo "$label secret path exists but is not a file." >&2
      exit 1
    fi
  else
    dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -d '\n=' | tr '+/' '-_' > "$secret_path"
  fi
  chmod 600 "$secret_path"
  if ! grep -Eq '^[A-Za-z0-9_-]{64}$' "$secret_path"; then
    echo "$label secret does not contain 48 random bytes in base64url form." >&2
    exit 1
  fi
}

initialize_secret "$SECRET_DIR/db_password" "Database"
initialize_secret "$SECRET_DIR/session_signing_secret" "Session signing"
echo "PASS: local database and session signing secrets exist with mode 600."
