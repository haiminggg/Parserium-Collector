param(
  [string]$LocalDirectory = (Join-Path $PSScriptRoot '..\.local'),
  [string]$SecretDirectory = '',
  [string]$ExportDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = [IO.Path]::GetFullPath((Join-Path $repository '.local'))
$localTarget = [IO.Path]::GetFullPath($LocalDirectory)
if (
  -not $localTarget.Equals($localRoot, [StringComparison]::OrdinalIgnoreCase) -and
  -not $localTarget.StartsWith($localRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
) {
  throw "Local directory escapes the ignored local root: $localTarget"
}
New-Item -ItemType Directory -Path $localTarget -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($SecretDirectory)) {
  $SecretDirectory = Join-Path $localTarget 'secrets'
}
$target = [IO.Path]::GetFullPath($SecretDirectory)
if (-not $target.StartsWith($repository.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Secret directory escapes the repository: $target"
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-ConfiguredValue {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $matches = @(
    Get-Content -LiteralPath $Path |
      Where-Object { $_ -match ('^{0}=' -f [Regex]::Escape($Name)) }
  )
  if ($matches.Count -ne 1) {
    throw "Local configuration must contain exactly one $Name entry."
  }
  return ($matches[0] -split '=', 2)[1]
}

function Test-WindowsAbsolutePath {
  param([Parameter(Mandatory = $true)][string]$Path)

  return (
    $Path -match '^[A-Za-z]:[\\/]' -or
    $Path -match '^\\\\[^\\/]+[\\/][^\\/]+'
  )
}

function Initialize-LocalConfiguration {
  param(
    [Parameter(Mandatory = $true)][string]$LocalPath,
    [string]$RequestedExportPath
  )

  $configPath = Join-Path $LocalPath 'config.env'
  if (Test-Path -LiteralPath $configPath) {
    $item = Get-Item -LiteralPath $configPath -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'Local configuration path exists but is not a regular file.'
    }
    $configuredExport = Get-ConfiguredValue -Path $configPath -Name 'PARSERIUM_EXPORT_ROOT'
    if (
      -not (Test-WindowsAbsolutePath -Path $configuredExport) -or
      -not (Test-Path -LiteralPath $configuredExport -PathType Container)
    ) {
      throw 'Configured export directory must be an absolute existing directory.'
    }
    return
  }

  if ([string]::IsNullOrWhiteSpace($RequestedExportPath)) {
    $resolvedExport = [IO.Path]::GetFullPath((Join-Path $LocalPath 'exports'))
    New-Item -ItemType Directory -Path $resolvedExport -Force | Out-Null
  } else {
    if (-not (Test-WindowsAbsolutePath -Path $RequestedExportPath)) {
      throw 'Export directory must be an absolute existing directory.'
    }
    $resolvedExport = [IO.Path]::GetFullPath($RequestedExportPath)
    if (-not (Test-Path -LiteralPath $resolvedExport -PathType Container)) {
      throw 'Export directory must be an absolute existing directory.'
    }
  }
  $composeExport = $resolvedExport.Replace('\', '/')
  $configuration = @(
    "PARSERIUM_EXPORT_ROOT=$composeExport",
    'DASHBOARD_EXPORT_ROOT=/exports',
    'DASHBOARD_DOWNLOAD_MAX_BYTES=104857600',
    'DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS=10',
    'DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS=60',
    'DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS=600',
    'DASHBOARD_DOWNLOAD_MAX_REDIRECTS=5',
    'DASHBOARD_DOWNLOAD_MAX_ATTEMPTS=3',
    'DASHBOARD_DOWNLOAD_RETRY_BASE_SECONDS=30',
    'DASHBOARD_DOWNLOAD_LEASE_SECONDS=60',
    'DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS=[80,443]',
    'DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST=[]',
    'DASHBOARD_DOCX_MAX_EXPANDED_BYTES=524288000',
    'DASHBOARD_DOCX_MAX_EXPANSION_RATIO=100'
  ) -join "`n"
  [IO.File]::WriteAllText(
    $configPath,
    $configuration + "`n",
    [Text.UTF8Encoding]::new($false)
  )
}

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
Initialize-LocalSecret -Path (Join-Path $target 'discovery_fingerprint_secret') -Label 'Discovery fingerprint'
Initialize-LocalConfiguration -LocalPath $localTarget -RequestedExportPath $ExportDirectory
Write-Output 'PASS: local secrets and acquisition configuration are ready.'
