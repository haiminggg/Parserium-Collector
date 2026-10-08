#!/usr/bin/env sh
set -eu
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
LOCAL_ROOT="$REPOSITORY/.local"
LOCAL_DIR=${1:-$LOCAL_ROOT}
REQUESTED_EXPORT_DIR=${2:-}
case "$LOCAL_DIR" in
  "$LOCAL_ROOT"|"$LOCAL_ROOT"/*) ;;
  *) echo "Local directory escapes the ignored local root: $LOCAL_DIR" >&2; exit 1 ;;
esac
mkdir -p "$LOCAL_DIR"
LOCAL_DIR=$(CDPATH= cd -- "$LOCAL_DIR" && pwd -P)
LOCAL_ROOT=$(CDPATH= cd -- "$LOCAL_ROOT" && pwd -P)
case "$LOCAL_DIR" in
  "$LOCAL_ROOT"|"$LOCAL_ROOT"/*) ;;
  *) echo "Local directory escapes the ignored local root: $LOCAL_DIR" >&2; exit 1 ;;
esac
SECRET_DIR="$LOCAL_DIR/secrets"
mkdir -p "$SECRET_DIR"

initialize_configuration() {
  config_path="$LOCAL_DIR/config.env"
  if [ -e "$config_path" ] || [ -L "$config_path" ]; then
    if [ ! -f "$config_path" ] || [ -L "$config_path" ]; then
      echo "Local configuration path exists but is not a regular file." >&2
      exit 1
    fi
    export_count=$(grep -Ec '^PARSERIUM_EXPORT_ROOT=' "$config_path" || true)
    if [ "$export_count" -ne 1 ]; then
      echo "Local configuration must contain exactly one PARSERIUM_EXPORT_ROOT entry." >&2
      exit 1
    fi
    configured_export=$(grep -E '^PARSERIUM_EXPORT_ROOT=' "$config_path")
    configured_export=${configured_export#*=}
    case "$configured_export" in
      /*) ;;
      *)
        echo "Configured export directory must be an absolute existing directory." >&2
        exit 1
        ;;
    esac
    if [ ! -d "$configured_export" ]; then
      echo "Configured export directory must be an absolute existing directory." >&2
      exit 1
    fi
    return
  fi

  if [ -n "$REQUESTED_EXPORT_DIR" ]; then
    case "$REQUESTED_EXPORT_DIR" in
      /*) ;;
      *)
        echo "Export directory must be an absolute existing directory." >&2
        exit 1
        ;;
    esac
    if [ ! -d "$REQUESTED_EXPORT_DIR" ]; then
      echo "Export directory must be an absolute existing directory." >&2
      exit 1
    fi
    export_dir=$(CDPATH= cd -- "$REQUESTED_EXPORT_DIR" && pwd -P)
  else
    export_dir="$LOCAL_DIR/exports"
    mkdir -p "$export_dir"
    export_dir=$(CDPATH= cd -- "$export_dir" && pwd -P)
  fi

  {
    echo "PARSERIUM_EXPORT_ROOT=$export_dir"
    echo 'DASHBOARD_EXPORT_ROOT=/exports'
    echo 'DASHBOARD_DOWNLOAD_MAX_BYTES=104857600'
    echo 'DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS=10'
    echo 'DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS=60'
    echo 'DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS=600'
    echo 'DASHBOARD_DOWNLOAD_MAX_REDIRECTS=5'
    echo 'DASHBOARD_DOWNLOAD_MAX_ATTEMPTS=3'
    echo 'DASHBOARD_DOWNLOAD_RETRY_BASE_SECONDS=30'
    echo 'DASHBOARD_DOWNLOAD_LEASE_SECONDS=60'
    echo 'DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS=[80,443]'
    echo 'DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST=[]'
    echo 'DASHBOARD_DOCX_MAX_EXPANDED_BYTES=524288000'
    echo 'DASHBOARD_DOCX_MAX_EXPANSION_RATIO=100'
  } > "$config_path"
  chmod 600 "$config_path"
}

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
initialize_secret "$SECRET_DIR/discovery_fingerprint_secret" "Discovery fingerprint"
initialize_configuration
echo "PASS: local secrets and acquisition configuration are ready."
