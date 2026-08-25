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
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Initialize-LocalSecret {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label
  )

  if (Test-Path -LiteralPath $Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      $isRegularFile = $true
    } else {
      $isRegularFile = $false
    }
    if (-not $isRegularFile) {
      throw "$Label secret path exists but is not a file."
    }
  } else {
    $bytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
      $generator.GetBytes($bytes)
    } finally {
      $generator.Dispose()
    }
    $value = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($Path, $value, [Text.UTF8Encoding]::new($false))
  }

  $value = [IO.File]::ReadAllText($Path)
  if ($value -notmatch '^[A-Za-z0-9_-]{64}$') {
    throw "$Label secret does not contain 48 random bytes in base64url form."
  }
  & icacls.exe $Path /reset | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not reset the $Label secret ACL." }
  & icacls.exe $Path /grant:r "${identity}:(F)" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not grant access to the $Label secret owner." }
  & icacls.exe $Path /inheritance:r | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not remove inherited $Label secret access." }
}

Initialize-LocalSecret -Path (Join-Path $target 'db_password') -Label 'Database'
Initialize-LocalSecret -Path (Join-Path $target 'session_signing_secret') -Label 'Session signing'
Write-Output 'PASS: local database and session signing secrets exist with user-only ACLs.'
