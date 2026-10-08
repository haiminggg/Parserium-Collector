#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
LOCAL_ROOT="$REPOSITORY/.local"
TEST_ROOT="$LOCAL_ROOT/bootstrap-config-test-$$"
case "$TEST_ROOT" in
  "$LOCAL_ROOT"/*) ;;
  *) echo "Test directory escapes the local root: $TEST_ROOT" >&2; exit 1 ;;
esac

cleanup() {
  case "$TEST_ROOT" in
    "$LOCAL_ROOT"/bootstrap-config-test-*) rm -rf -- "$TEST_ROOT" ;;
    *) echo "Refusing cleanup for unexpected test path: $TEST_ROOT" >&2; exit 1 ;;
  esac
}
trap cleanup EXIT INT TERM
mkdir -p "$TEST_ROOT"

default_local="$TEST_ROOT/default"
sh "$REPOSITORY/scripts/bootstrap-local.sh" "$default_local" >/dev/null
test -d "$default_local/exports"
test -f "$default_local/config.env"
grep -Fqx "PARSERIUM_EXPORT_ROOT=$default_local/exports" "$default_local/config.env"
for required in \
  DASHBOARD_DOWNLOAD_MAX_BYTES \
  DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS \
  DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS \
  DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS \
  DASHBOARD_DOWNLOAD_MAX_REDIRECTS \
  DASHBOARD_DOWNLOAD_MAX_ATTEMPTS \
  DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS \
  DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST \
  DASHBOARD_DOCX_MAX_EXPANDED_BYTES \
  DASHBOARD_DOCX_MAX_EXPANSION_RATIO
do
  grep -Eq "^${required}=" "$default_local/config.env"
done

custom_local="$TEST_ROOT/custom"
custom_export="$TEST_ROOT/chosen-export-root"
mkdir -p "$custom_export"
sh "$REPOSITORY/scripts/bootstrap-local.sh" "$custom_local" "$custom_export" >/dev/null
sed 's/DASHBOARD_DOWNLOAD_MAX_BYTES=104857600/DASHBOARD_DOWNLOAD_MAX_BYTES=209715200/' \
  "$custom_local/config.env" > "$custom_local/config.env.updated"
mv "$custom_local/config.env.updated" "$custom_local/config.env"
before=$(sha256sum "$custom_local/config.env")
sh "$REPOSITORY/scripts/bootstrap-local.sh" "$custom_local" >/dev/null
after=$(sha256sum "$custom_local/config.env")
test "$before" = "$after"

invalid_local="$TEST_ROOT/invalid"
mkdir -p "$invalid_local"
printf '%s\n' 'PARSERIUM_EXPORT_ROOT=relative/exports' > "$invalid_local/config.env"
if invalid_output=$(sh "$REPOSITORY/scripts/bootstrap-local.sh" "$invalid_local" 2>&1); then
  echo 'Relative configured export path unexpectedly succeeded.' >&2
  exit 1
fi
printf '%s\n' "$invalid_output" | grep -Fq \
  'Configured export directory must be an absolute existing directory.'

echo 'PASS: POSIX bootstrap creates and preserves validated local acquisition configuration.'
