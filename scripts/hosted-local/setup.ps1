param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
$common = Join-Path $PSScriptRoot 'common.ps1'
if (-not (Test-Path -LiteralPath $common -PathType Leaf)) {
  throw "Hosted-local common library is missing: $common"
}
. $common

if ($env:OS -ne 'Windows_NT') {
  throw 'Hosted-local setup requires Windows.'
}
if ($PSVersionTable.PSVersion.Major -lt 5) {
  throw 'Hosted-local setup requires PowerShell 5.1 or newer.'
}
if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw 'Docker is not installed or is not available on PATH.'
}
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw 'Docker Compose is not available.'
}

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot `
  -StateRoot $resolvedStateRoot `
  -RepositoryRoot $repository

New-Item -ItemType Directory -Path $resolvedStateRoot -Force | Out-Null
$configDirectory = Join-Path $resolvedStateRoot 'config'
$secretDirectory = Join-Path $resolvedStateRoot 'secrets'
foreach ($directory in @($configDirectory, $secretDirectory)) {
  if (Test-Path -LiteralPath $directory) {
    $item = Get-Item -LiteralPath $directory -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Hosted-local directory path has an unsafe collision: $directory"
    }
  } else {
    New-Item -ItemType Directory -Path $directory | Out-Null
  }
  Assert-NoReparsePointInPath -Path $directory
}

function Initialize-HostedLocalSecret {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$Pattern,
    [Parameter(Mandatory = $true)][scriptblock]$Factory
  )

  if (Test-Path -LiteralPath $Path) {
    Assert-RegularReadableFile -Path $Path -Label "$Label secret"
  } else {
    $value = & $Factory
    if ($value -notmatch $Pattern) {
      throw "$Label secret generation produced an invalid value."
    }
    Write-AtomicUtf8File -Path $Path -Value $value
  }
  $existing = [IO.File]::ReadAllText($Path)
  if ($existing -notmatch $Pattern) {
    throw "$Label secret does not match the required format."
  }
}

$secretPaths = @{
  Database = Join-Path $secretDirectory 'db_password'
  Session = Join-Path $secretDirectory 'session_signing_secret'
  MinioAccess = Join-Path $secretDirectory 'minio_access_key'
  MinioSecret = Join-Path $secretDirectory 'minio_secret_key'
  FirecrawlWrapping = Join-Path $secretDirectory 'firecrawl_credential_wrapping_key'
  DiscoveryFingerprint = Join-Path $secretDirectory 'discovery_fingerprint_secret'
}
Initialize-HostedLocalSecret `
  -Path $secretPaths.Database `
  -Label 'Database' `
  -Pattern '^[A-Za-z0-9_-]{64}$' `
  -Factory { New-RandomBase64UrlSecret -Length 64 }
Initialize-HostedLocalSecret `
  -Path $secretPaths.Session `
  -Label 'Session signing' `
  -Pattern '^[A-Za-z0-9_-]{64}$' `
  -Factory { New-RandomBase64UrlSecret -Length 64 }
Initialize-HostedLocalSecret `
  -Path $secretPaths.MinioAccess `
  -Label 'MinIO access key' `
  -Pattern '^parserium-[A-Za-z0-9_-]{32}$' `
  -Factory { 'parserium-' + (New-RandomBase64UrlSecret -Length 32) }
Initialize-HostedLocalSecret `
  -Path $secretPaths.MinioSecret `
  -Label 'MinIO secret key' `
  -Pattern '^[A-Za-z0-9_-]{64}$' `
  -Factory { New-RandomBase64UrlSecret -Length 64 }
Initialize-HostedLocalSecret `
  -Path $secretPaths.DiscoveryFingerprint `
  -Label 'Discovery fingerprint' `
  -Pattern '^[A-Za-z0-9_-]{64}$' `
  -Factory { New-RandomBase64UrlSecret -Length 64 }
if (-not (Test-Path -LiteralPath $secretPaths.FirecrawlWrapping)) {
  Write-AtomicUtf8File `
    -Path $secretPaths.FirecrawlWrapping `
    -Value (New-HostedLocalCredentialWrappingKey)
}
Assert-HostedLocalCredentialWrappingKey -Path $secretPaths.FirecrawlWrapping

$configurationPath = Join-Path $configDirectory 'hosted-local.env'
if (Test-Path -LiteralPath $configurationPath) {
  Assert-RegularReadableFile -Path $configurationPath -Label 'Hosted-local configuration'
  $existingLines = @([IO.File]::ReadAllLines($configurationPath))
  $migratedLines = @(
    $existingLines |
      Where-Object { $_ -notmatch '^DASHBOARD_FIRECRAWL_BASE_URL=' }
  )
  if ($migratedLines.Count -ne $existingLines.Count) {
    Write-AtomicUtf8File `
      -Path $configurationPath `
      -Value (($migratedLines -join "`n") + "`n")
  }
} else {
  $configuration = @(
    'DASHBOARD_BUILD_ID=hosted-local',
    'DASHBOARD_RELEASE_VERSION=0.1.0-hosted-local',
    'DASHBOARD_DEPLOYMENT_MODE=hosted',
    'DASHBOARD_PUBLIC_ORIGIN=https://localhost:8443',
    'DASHBOARD_ALLOWED_HOSTS=["localhost","127.0.0.1"]',
    'DASHBOARD_ALLOWED_ORIGINS=["https://localhost:8443","https://127.0.0.1:8443"]',
    'DASHBOARD_SESSION_COOKIE_SECURE=true',
    'DASHBOARD_STORAGE_BACKEND=s3',
    'DASHBOARD_STORAGE_ENDPOINT=https://minio:9000',
    'DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT=https://localhost:9000',
    'DASHBOARD_STORAGE_REGION=us-east-1',
    'DASHBOARD_STORAGE_BUCKET=parserium-hosted-local',
    'DASHBOARD_STORAGE_FORCE_PATH_STYLE=true',
    'DASHBOARD_STORAGE_SIGNED_URL_TTL_SECONDS=60',
    'DASHBOARD_SCRATCH_ROOT=/var/lib/parserium-collector-scratch',
    'DASHBOARD_STATIC_ROOT=/app/static',
    'DASHBOARD_OIDC_ISSUER=https://accounts.google.com',
    'DASHBOARD_OIDC_PROVIDER_LABEL=Google',
    'DASHBOARD_OIDC_REDIRECT_URI=https://localhost:8443/api/v1/auth/callback'
  ) -join "`n"
  Write-AtomicUtf8File -Path $configurationPath -Value ($configuration + "`n")
}

& docker build --quiet --target runtime --tag parserium-collector:hosted-local $repository | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw 'Could not build parserium-collector:hosted-local.'
}
& docker build --quiet --target minio-hosted-local --tag parserium-minio:hosted-local $repository | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw 'Could not build parserium-minio:hosted-local.'
}

function Assert-CompleteHostedLocalPki {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "Hosted-local PKI directory is missing: $Path"
  }
  Assert-NoReparsePointInPath -Path $Path
  $expectedNames = @(
    'ca-key.pem',
    'ca.pem',
    'minio-cert.pem',
    'minio-key.pem',
    'parserium-cert.pem',
    'parserium-key.pem'
  )
  $items = @(Get-ChildItem -LiteralPath $Path -Force)
  if (($items | Where-Object { $_.PSIsContainer }).Count -ne 0) {
    throw 'Hosted-local PKI directory contains an unexpected directory.'
  }
  $actualNames = @($items.Name | Sort-Object)
  if (($actualNames -join "`n") -ne (($expectedNames | Sort-Object) -join "`n")) {
    throw 'Hosted-local PKI directory does not contain the exact required files.'
  }
  foreach ($name in $expectedNames) {
    Assert-RegularReadableFile -Path (Join-Path $Path $name) -Label "Hosted-local PKI file $name"
  }
}

$pkiPath = Join-Path $resolvedStateRoot 'pki'
if (Test-Path -LiteralPath $pkiPath) {
  $pkiItem = Get-Item -LiteralPath $pkiPath -Force
  if (-not $pkiItem.PSIsContainer -or ($pkiItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Hosted-local PKI path has an unsafe collision: $pkiPath"
  }
  Assert-CompleteHostedLocalPki -Path $pkiPath
} else {
  $pkiStaging = Join-Path $resolvedStateRoot ('pki-staging-' + [Guid]::NewGuid().ToString('N'))
  try {
    New-Item -ItemType Directory -Path $pkiStaging | Out-Null
    $stateParent = Split-Path -Parent $resolvedStateRoot
    $stateName = Split-Path -Leaf $resolvedStateRoot
    $mount = "${stateParent}:/host-state"
    $containerOutput = "/host-state/${stateName}/$([IO.Path]::GetFileName($pkiStaging))"
    & docker run --rm --volume $mount `
      parserium-collector:hosted-local `
      python -m parserium_collector.cli.development_pki $containerOutput | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw 'Hosted-local PKI generation failed.'
    }
    Assert-CompleteHostedLocalPki -Path $pkiStaging
    if (Test-Path -LiteralPath $pkiPath) {
      throw "Hosted-local PKI destination appeared during setup: $pkiPath"
    }
    Move-Item -LiteralPath $pkiStaging -Destination $pkiPath
  } finally {
    if (Test-Path -LiteralPath $pkiStaging) {
      Remove-Item -LiteralPath $pkiStaging -Recurse -Force
    }
  }
}

Set-HostedLocalPkiContainerOwner `
  -StateRoot $resolvedStateRoot `
  -PkiDirectoryName 'pki'

Protect-HostedLocalPath -Path $resolvedStateRoot
Protect-HostedLocalPath -Path $secretDirectory
foreach ($secretPath in $secretPaths.Values) {
  Protect-HostedLocalPath -Path $secretPath
}
Protect-HostedLocalPath -Path (Join-Path $pkiPath 'ca-key.pem')
Protect-HostedLocalPath -Path (Join-Path $pkiPath 'parserium-key.pem')
Protect-HostedLocalPath -Path (Join-Path $pkiPath 'minio-key.pem')

Write-Output "Hosted-local state is ready at: $resolvedStateRoot"
Write-Output "Next, import a Google Web client: .\scripts\hosted-local\configure-google.ps1 -ClientJson <path> -StateRoot `"$resolvedStateRoot`""
Write-Output "Then install the development CA: .\scripts\hosted-local\install-ca.ps1 -StateRoot `"$resolvedStateRoot`""
Write-Output 'After sign-in, open Connections to add and validate a Firecrawl connection for the workspace.'
