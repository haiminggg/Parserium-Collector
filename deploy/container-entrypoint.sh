#!/usr/bin/env sh
set -eu
umask 077

if [ "$#" -eq 0 ]; then
  echo 'Container entrypoint requires a command.' >&2
  exit 64
fi

if [ "$(id -u)" -ne 0 ] || [ "${PARSERIUM_KEEP_ROOT:-0}" = "1" ]; then
  exec "$@"
fi

runtime_secret_dir=/tmp/parserium-secrets
install -d -m 0711 -o 0 -g 0 "$runtime_secret_dir"

if [ -f /run/secrets/db_password ]; then
  install -m 0400 /run/secrets/db_password "$runtime_secret_dir/db_password"
  chown 10001:10001 "$runtime_secret_dir/db_password"
  export DASHBOARD_DB_PASSWORD_FILE="$runtime_secret_dir/db_password"
fi

if [ -f /run/secrets/session_signing_secret ]; then
  install -m 0400 \
    /run/secrets/session_signing_secret "$runtime_secret_dir/session_signing_secret"
  chown 10001:10001 "$runtime_secret_dir/session_signing_secret"
  export DASHBOARD_SESSION_SIGNING_SECRET_FILE="$runtime_secret_dir/session_signing_secret"
fi

if [ -f /run/secrets/oidc_client_secret ]; then
  install -m 0400 /run/secrets/oidc_client_secret "$runtime_secret_dir/oidc_client_secret"
  chown 10001:10001 "$runtime_secret_dir/oidc_client_secret"
  export DASHBOARD_OIDC_CLIENT_SECRET_FILE="$runtime_secret_dir/oidc_client_secret"
  export OIDC_CLIENT_SECRET_FILE="$runtime_secret_dir/oidc_client_secret"
fi

if [ -f /run/secrets/oidc_ca_bundle ]; then
  install -m 0444 /run/secrets/oidc_ca_bundle "$runtime_secret_dir/oidc_ca_bundle"
  chown 10001:10001 "$runtime_secret_dir/oidc_ca_bundle"
  export DASHBOARD_OIDC_CA_BUNDLE_FILE="$runtime_secret_dir/oidc_ca_bundle"
fi

if [ -f /run/secrets/storage_access_key ]; then
  install -m 0400 /run/secrets/storage_access_key "$runtime_secret_dir/storage_access_key"
  chown 10001:10001 "$runtime_secret_dir/storage_access_key"
  export DASHBOARD_STORAGE_ACCESS_KEY_FILE="$runtime_secret_dir/storage_access_key"
  export TEST_S3_ACCESS_KEY_FILE="$runtime_secret_dir/storage_access_key"
fi

if [ -f /run/secrets/storage_secret_key ]; then
  install -m 0400 /run/secrets/storage_secret_key "$runtime_secret_dir/storage_secret_key"
  chown 10001:10001 "$runtime_secret_dir/storage_secret_key"
  export DASHBOARD_STORAGE_SECRET_KEY_FILE="$runtime_secret_dir/storage_secret_key"
  export TEST_S3_SECRET_KEY_FILE="$runtime_secret_dir/storage_secret_key"
fi

if [ -f /run/secrets/storage_ca_bundle ]; then
  install -m 0444 /run/secrets/storage_ca_bundle "$runtime_secret_dir/storage_ca_bundle"
  chown 10001:10001 "$runtime_secret_dir/storage_ca_bundle"
  export DASHBOARD_STORAGE_CA_BUNDLE_FILE="$runtime_secret_dir/storage_ca_bundle"
  export TEST_S3_CA_BUNDLE_FILE="$runtime_secret_dir/storage_ca_bundle"
  if [ "${SSL_CERT_FILE:-}" = "/run/secrets/storage_ca_bundle" ]; then
    export SSL_CERT_FILE="$runtime_secret_dir/storage_ca_bundle"
  fi
fi

if [ -f /run/secrets/firecrawl_credential_wrapping_key ]; then
  install -m 0400 \
    /run/secrets/firecrawl_credential_wrapping_key \
    "$runtime_secret_dir/firecrawl_credential_wrapping_key"
  chown 10001:10001 "$runtime_secret_dir/firecrawl_credential_wrapping_key"
  export DASHBOARD_CREDENTIAL_ENCRYPTION_KEY_FILE="$runtime_secret_dir/firecrawl_credential_wrapping_key"
fi

if [ -f /run/secrets/firecrawl_test_bearer ]; then
  install -m 0400 \
    /run/secrets/firecrawl_test_bearer "$runtime_secret_dir/firecrawl_test_bearer"
  chown 10001:10001 "$runtime_secret_dir/firecrawl_test_bearer"
  export TEST_FIRECRAWL_BEARER_FILE="$runtime_secret_dir/firecrawl_test_bearer"
fi

if [ -f /run/secrets/tls_certificate ]; then
  install -m 0400 /run/secrets/tls_certificate "$runtime_secret_dir/tls_certificate"
  chown 10001:10001 "$runtime_secret_dir/tls_certificate"
  export TLS_CERTIFICATE_FILE="$runtime_secret_dir/tls_certificate"
fi

if [ -f /run/secrets/tls_private_key ]; then
  install -m 0400 /run/secrets/tls_private_key "$runtime_secret_dir/tls_private_key"
  chown 10001:10001 "$runtime_secret_dir/tls_private_key"
  export TLS_KEY_FILE="$runtime_secret_dir/tls_private_key"
fi

if [ -f /run/secrets/oidc_signing_private_key ]; then
  install -m 0400 \
    /run/secrets/oidc_signing_private_key "$runtime_secret_dir/oidc_signing_private_key"
  chown 10001:10001 "$runtime_secret_dir/oidc_signing_private_key"
  export OIDC_SIGNING_KEY_FILE="$runtime_secret_dir/oidc_signing_private_key"
fi

chown 10001:10001 "$runtime_secret_dir"

exec setpriv \
  --reuid=10001 \
  --regid=10001 \
  --clear-groups \
  --inh-caps=-all \
  --ambient-caps=-all \
  --bounding-set=-all \
  --no-new-privs \
  -- "$@"
