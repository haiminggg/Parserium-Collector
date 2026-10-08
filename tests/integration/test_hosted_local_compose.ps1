$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$hostedCompose = Join-Path $repository 'deploy\hosted-local\compose.yaml'
if (-not (Test-Path -LiteralPath $hostedCompose -PathType Leaf)) {
  throw "Hosted-local Compose file is missing: $hostedCompose"
}
$developmentCompose = Join-Path $repository 'deploy\development\compose.yaml'
$localConfig = Join-Path $repository '.local\config.env'
$checker = Join-Path $repository 'scripts\check_compose.py'
$python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
. (Join-Path $repository 'scripts\hosted-local\common.ps1')
. (Join-Path $repository 'scripts\hosted-local\lifecycle.ps1')
$suffix = [Guid]::NewGuid().ToString('N')
$stateRoot = [IO.Path]::GetFullPath(
  (Join-Path ([IO.Path]::GetTempPath()) "parserium-hosted-local-compose-state-$suffix")
)
$testRoot = [IO.Path]::GetFullPath(
  (Join-Path ([IO.Path]::GetTempPath()) "parserium-hosted-local-compose-cases-$suffix")
)
$previousStateRoot = $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT
$previousConfigFile = $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE

function Write-TestFile {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][object]$Value
  )

  $text = if ($Value -is [Array]) {
    $Value -join [Environment]::NewLine
  } else {
    [string]$Value
  }
  [IO.File]::WriteAllText($Path, $text, [Text.UTF8Encoding]::new($false))
}

function Assert-HostedCheckerFailure {
  param(
    [Parameter(Mandatory = $true)][object]$Document,
    [Parameter(Mandatory = $true)][string]$Expected,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$BasePath,
    [Parameter(Mandatory = $true)][string]$HostedConfig
  )

  $casePath = Join-Path $testRoot "$Name.json"
  Write-TestFile -Path $casePath -Value ($Document | ConvertTo-Json -Depth 100)
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = & $python $checker $BasePath `
      --local-config $localConfig `
      --hosted-local-config $casePath `
      --hosted-local-state-root $stateRoot 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -eq 0) {
    throw "$Name unexpectedly passed the hosted-local Compose policy checker."
  }
  if (($output -join [Environment]::NewLine) -notmatch [Regex]::Escape($Expected)) {
    throw "$Name returned an unexpected policy result: $output"
  }
}

try {
  New-Item -ItemType Directory -Path (Join-Path $stateRoot 'config') -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $stateRoot 'secrets') -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $stateRoot 'pki') -Force | Out-Null
  New-Item -ItemType Directory -Path $testRoot | Out-Null
  $configurationPath = Join-Path $stateRoot 'config\hosted-local.env'
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
    'DASHBOARD_OIDC_REDIRECT_URI=https://localhost:8443/api/v1/auth/callback',
    'DASHBOARD_OIDC_CLIENT_ID=test-client.apps.googleusercontent.com'
  ) -join "`n"
  Write-TestFile -Path $configurationPath -Value ($configuration + "`n")

  $secretValues = @{
    db_password = 'test-db-password-unique-hosted-local'
    session_signing_secret = 'test-session-secret-unique-hosted-local'
    google_client_secret = 'test-google-secret-unique-hosted-local'
    minio_access_key = 'parserium-test-access-key'
    minio_secret_key = 'test-minio-secret-unique-hosted-local'
    firecrawl_credential_wrapping_key = [Convert]::ToBase64String((New-Object byte[] 32))
    discovery_fingerprint_secret = 'test-discovery-fingerprint-secret-unique-hosted-local-value-0001'
  }
  foreach ($entry in $secretValues.GetEnumerator()) {
    Write-TestFile -Path (Join-Path $stateRoot "secrets\$($entry.Key)") -Value $entry.Value
  }
  foreach ($name in @(
    'ca.pem',
    'ca-key.pem',
    'parserium-cert.pem',
    'parserium-key.pem',
    'minio-cert.pem',
    'minio-key.pem'
  )) {
    Write-TestFile -Path (Join-Path $stateRoot "pki\$name") -Value "test-$name-content"
  }

  if (-not (Test-Path -LiteralPath $localConfig -PathType Leaf)) {
    & (Join-Path $repository 'scripts\bootstrap-local.ps1') | Out-Null
  }
  $baseJson = docker compose `
    --env-file $localConfig `
    -f $developmentCompose `
    config `
    --format json
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not render the base development Compose file.'
  }
  $basePath = Join-Path $testRoot 'base.json'
  Write-TestFile -Path $basePath -Value $baseJson

  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $stateRoot.Replace('\', '/')
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = $configurationPath.Replace('\', '/')
  $hostedJson = docker compose `
    --project-name parserium-hosted-local `
    --env-file $configurationPath `
    -f $hostedCompose `
    config `
    --format json
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not render hosted-local Compose.'
  }
  $hostedPath = Join-Path $testRoot 'hosted.json'
  Write-TestFile -Path $hostedPath -Value $hostedJson

  & $python $checker $basePath `
    --local-config $localConfig `
    --hosted-local-config $hostedPath `
    --hosted-local-state-root $stateRoot
  if ($LASTEXITCODE -ne 0) {
    throw 'The unmodified hosted-local Compose file failed policy validation.'
  }
  Assert-HostedLocalComposePolicy `
    -RenderedJson ($hostedJson -join "`n") `
    -StateRoot $stateRoot

  $projectDrift = $hostedJson | ConvertFrom-Json
  $projectDrift.name = 'parserium-hosted-wrong'
  Assert-HostedCheckerFailure -Document $projectDrift -Expected 'project name' `
    -Name 'project-drift' -BasePath $basePath -HostedConfig $hostedPath

  $lanApi = $hostedJson | ConvertFrom-Json
  $lanApi.services.api.ports[0].host_ip = '0.0.0.0'
  Assert-HostedCheckerFailure -Document $lanApi -Expected 'loopback' `
    -Name 'lan-api' -BasePath $basePath -HostedConfig $hostedPath

  $publishedDatabase = $hostedJson | ConvertFrom-Json
  $publishedDatabase.services.db | Add-Member -NotePropertyName ports -NotePropertyValue @(
    [PSCustomObject]@{ target = 5432; published = '5432'; host_ip = '127.0.0.1' }
  )
  Assert-HostedCheckerFailure -Document $publishedDatabase -Expected 'db must not publish' `
    -Name 'published-database' -BasePath $basePath -HostedConfig $hostedPath

  $httpStorage = $hostedJson | ConvertFrom-Json
  $httpStorage.services.worker.environment.DASHBOARD_STORAGE_ENDPOINT = 'http://minio:9000'
  Assert-HostedCheckerFailure -Document $httpStorage -Expected 'private TLS S3 endpoint' `
    -Name 'http-storage' -BasePath $basePath -HostedConfig $hostedPath

  $filesystemStorage = $hostedJson | ConvertFrom-Json
  $filesystemStorage.services.api.environment.DASHBOARD_STORAGE_BACKEND = 'filesystem'
  Assert-HostedCheckerFailure -Document $filesystemStorage -Expected 'S3 durable storage' `
    -Name 'filesystem-storage' -BasePath $basePath -HostedConfig $hostedPath

  $missingSecret = $hostedJson | ConvertFrom-Json
  $missingSecret.services.api.secrets = @(
    $missingSecret.services.api.secrets |
      Where-Object { $_.source -ne 'session_signing_secret' }
  )
  Assert-HostedCheckerFailure -Document $missingSecret -Expected 'exact required secrets' `
    -Name 'missing-secret' -BasePath $basePath -HostedConfig $hostedPath

  $rawSecret = $hostedJson | ConvertFrom-Json
  $rawSecret.services.api.environment | Add-Member `
    -NotePropertyName DASHBOARD_SESSION_SIGNING_SECRET `
    -NotePropertyValue $secretValues.session_signing_secret
  Assert-HostedCheckerFailure -Document $rawSecret -Expected 'raw secret' `
    -Name 'raw-secret' -BasePath $basePath -HostedConfig $hostedPath

  $minioEgress = $hostedJson | ConvertFrom-Json
  $minioEgress.services.minio.networks | Add-Member -NotePropertyName edge -NotePropertyValue $null
  Assert-HostedCheckerFailure -Document $minioEgress -Expected 'minio must join only private' `
    -Name 'minio-egress' -BasePath $basePath -HostedConfig $hostedPath

  $minioConsole = $hostedJson | ConvertFrom-Json
  $minioConsole.services.minio.command = @(
    'server', '/data', '--console-address', ':9001'
  )
  Assert-HostedCheckerFailure -Document $minioConsole -Expected 'administrative console' `
    -Name 'minio-console' -BasePath $basePath -HostedConfig $hostedPath

  $selfHostedImage = $hostedJson | ConvertFrom-Json
  $selfHostedImage.services.api.image = 'parserium-collector:dev'
  Assert-HostedCheckerFailure -Document $selfHostedImage -Expected 'hosted-local image tag' `
    -Name 'self-hosted-image' -BasePath $basePath -HostedConfig $hostedPath

  $selfHostedVolume = $hostedJson | ConvertFrom-Json
  $selfHostedVolume.services.db.volumes[0].source = 'parserium-collector-dev_database'
  Assert-HostedCheckerFailure -Document $selfHostedVolume -Expected 'project-specific volume' `
    -Name 'self-hosted-volume' -BasePath $basePath -HostedConfig $hostedPath

  $wrongSignedEndpoint = $hostedJson | ConvertFrom-Json
  $wrongSignedEndpoint.services.api.environment.DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT = (
    'https://127.0.0.1:9000'
  )
  Assert-HostedCheckerFailure -Document $wrongSignedEndpoint -Expected 'signed URL endpoint' `
    -Name 'wrong-signed-endpoint' -BasePath $basePath -HostedConfig $hostedPath

  $missingWrappingKey = $hostedJson | ConvertFrom-Json
  $missingWrappingKey.secrets.PSObject.Properties.Remove(
    'firecrawl_credential_wrapping_key'
  )
  Assert-HostedCheckerFailure -Document $missingWrappingKey `
    -Expected 'credential wrapping key secret is missing' `
    -Name 'missing-wrapping-key' -BasePath $basePath -HostedConfig $hostedPath

  $globalFirecrawl = $hostedJson | ConvertFrom-Json
  $globalFirecrawl.services.api.environment | Add-Member `
    -NotePropertyName DASHBOARD_FIRECRAWL_BASE_URL `
    -NotePropertyValue 'https://api.firecrawl.dev'
  Assert-HostedCheckerFailure -Document $globalFirecrawl `
    -Expected 'must not configure a global Firecrawl endpoint' `
    -Name 'global-firecrawl-endpoint' -BasePath $basePath -HostedConfig $hostedPath

  foreach ($allowlistCase in @(
      @{ Name = 'cidr-private-allowlist'; Value = '["10.0.0.0/8"]' },
      @{ Name = 'wildcard-private-allowlist'; Value = '["*.internal.example"]' }
    )) {
    $unsafeAllowlist = $hostedJson | ConvertFrom-Json
    $unsafeAllowlist.services.api.environment | Add-Member `
      -NotePropertyName DASHBOARD_FIRECRAWL_REMOTE_PRIVATE_ALLOWLIST `
      -NotePropertyValue $allowlistCase.Value
    Assert-HostedCheckerFailure -Document $unsafeAllowlist `
      -Expected 'private allowlist must contain exact hostnames only' `
      -Name $allowlistCase.Name -BasePath $basePath -HostedConfig $hostedPath
  }

  $unrelatedWrappingKey = $hostedJson | ConvertFrom-Json
  $unrelatedWrappingKey.services.db.secrets += [PSCustomObject]@{
    source = 'firecrawl_credential_wrapping_key'
    target = 'firecrawl_credential_wrapping_key'
  }
  Assert-HostedCheckerFailure -Document $unrelatedWrappingKey `
    -Expected 'credential wrapping key must be mounted only by API and worker' `
    -Name 'db-wrapping-key' -BasePath $basePath -HostedConfig $hostedPath

  Write-Output 'PASS: hosted-local Compose policy rejects unsafe topology mutations.'
} finally {
  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $previousStateRoot
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = $previousConfigFile
  $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
  foreach ($path in @($stateRoot, $testRoot)) {
    $resolved = [IO.Path]::GetFullPath($path)
    if (-not $resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove an unexpected hosted-local Compose test path: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
      Remove-Item -LiteralPath $resolved -Recurse -Force
    }
  }
}
