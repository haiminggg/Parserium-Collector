$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRoot = [IO.Path]::GetFullPath((Join-Path $repository '.local'))
$testRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot ("secret-handoff-test-{0}" -f [Guid]::NewGuid().ToString('N')))
)
if (-not $testRoot.StartsWith($localRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Test directory escapes the local root: $testRoot"
}

try {
  New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
  $secretPath = Join-Path $testRoot 'db_password'
  [IO.File]::WriteAllText($secretPath, ('a' * 64), [Text.UTF8Encoding]::new($false))
  $wrappingKeyPath = Join-Path $testRoot 'firecrawl_credential_wrapping_key'
  [IO.File]::WriteAllText($wrappingKeyPath, ('b' * 44), [Text.UTF8Encoding]::new($false))
  $firecrawlBearerPath = Join-Path $testRoot 'firecrawl_test_bearer'
  [IO.File]::WriteAllText($firecrawlBearerPath, ('c' * 64), [Text.UTF8Encoding]::new($false))
  $caBundlePath = Join-Path $testRoot 'storage_ca_bundle'
  [IO.File]::WriteAllText($caBundlePath, 'test-ca', [Text.UTF8Encoding]::new($false))
  $fixturePath = Join-Path $repository 'tests\fixtures\verify_secret_handoff.py'
  if ($IsLinux) {
    # Real secrets are private to the host user. Prove the root entrypoint can still read them.
    & chmod 600 $secretPath $wrappingKeyPath $firecrawlBearerPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict test secret permissions.' }
  }
  docker run --rm `
    --read-only `
    --tmpfs /tmp:rw,noexec,nosuid,nodev `
    --cap-drop ALL `
    --cap-add CHOWN `
    --cap-add DAC_READ_SEARCH `
    --cap-add SETUID `
    --cap-add SETGID `
    --cap-add SETPCAP `
    --security-opt no-new-privileges:true `
    --user 0:0 `
    --env SSL_CERT_FILE=/run/secrets/storage_ca_bundle `
    --volume "${secretPath}:/run/secrets/db_password:ro" `
    --volume "${wrappingKeyPath}:/run/secrets/firecrawl_credential_wrapping_key:ro" `
    --volume "${firecrawlBearerPath}:/run/secrets/firecrawl_test_bearer:ro" `
    --volume "${caBundlePath}:/run/secrets/storage_ca_bundle:ro" `
    --volume "${fixturePath}:/verification/verify_secret_handoff.py:ro" `
    --entrypoint /usr/local/bin/parserium-entrypoint `
    parserium-collector:dev `
    python /verification/verify_secret_handoff.py
  if ($LASTEXITCODE -ne 0) {
    throw 'Container secret handoff verification failed.'
  }
} finally {
  if (Test-Path -LiteralPath $testRoot) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
  }
}
