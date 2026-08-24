param(
  [string]$SecretDirectory = (Join-Path $PSScriptRoot '..\.local\secrets')
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target = [IO.Path]::GetFullPath($SecretDirectory)
if (-not $target.StartsWith($repository.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Secret directory escapes the repository: $target"
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
$secretPath = Join-Path $target 'db_password'
if (-not (Test-Path -LiteralPath $secretPath)) {
  $bytes = New-Object byte[] 48
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
  } finally {
    $generator.Dispose()
  }
  $value = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  [IO.File]::WriteAllText($secretPath, $value, [Text.UTF8Encoding]::new($false))
}
$value = [IO.File]::ReadAllText($secretPath)
if ($value -notmatch '^[A-Za-z0-9_-]{64}$') {
  throw 'Database secret does not contain 48 random bytes in base64url form.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $secretPath /reset | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not reset the database secret ACL.' }
& icacls.exe $secretPath /grant:r "${identity}:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not grant access to the database secret owner.' }
& icacls.exe $secretPath /inheritance:r | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not remove inherited database secret access.' }
Write-Output 'PASS: local database secret exists with a user-only ACL.'
