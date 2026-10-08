$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRoot = [IO.Path]::GetFullPath((Join-Path $repository '.local'))
$testRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot ("bootstrap-test-{0}" -f [Guid]::NewGuid().ToString('N')))
)
if (-not $testRoot.StartsWith($localRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Test directory escapes the local root: $testRoot"
}

try {
  $invalidDirectory = Join-Path $testRoot 'invalid'
  New-Item -ItemType Directory -Path (Join-Path $invalidDirectory 'db_password') -Force | Out-Null
  $failure = $null
  try {
    & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
      -LocalDirectory (Join-Path $testRoot 'invalid-local') `
      -SecretDirectory $invalidDirectory
  } catch {
    $failure = $_.Exception.Message
  }
  if ($failure -ne 'Database secret path exists but is not a file.') {
    throw "Unexpected invalid-path result: $failure"
  }

  $validDirectory = Join-Path $testRoot 'valid'
  $output = & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
    -LocalDirectory (Join-Path $testRoot 'valid-local') `
    -SecretDirectory $validDirectory
  $databaseSecret = [IO.File]::ReadAllText((Join-Path $validDirectory 'db_password'))
  $sessionSecret = [IO.File]::ReadAllText((Join-Path $validDirectory 'session_signing_secret'))
  if ($databaseSecret -notmatch '^[A-Za-z0-9_-]{64}$') {
    throw 'Database secret is not a 64-character base64url value.'
  }
  if ($sessionSecret -notmatch '^[A-Za-z0-9_-]{64}$') {
    throw 'Session signing secret is not a 64-character base64url value.'
  }
  if (($output -join [Environment]::NewLine).Contains($databaseSecret)) {
    throw 'Bootstrap printed the database secret.'
  }
  if (($output -join [Environment]::NewLine).Contains($sessionSecret)) {
    throw 'Bootstrap printed the session signing secret.'
  }
  Write-Output 'PASS: bootstrap creates both secrets and rejects directory collisions.'
} finally {
  if (Test-Path -LiteralPath $testRoot) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
  }
}
