param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'pki.ps1')
. (Join-Path $PSScriptRoot 'lifecycle.ps1')

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
Assert-SafeHostedLocalStateRoot -StateRoot $resolvedStateRoot -RepositoryRoot $repository
if (-not (Test-Path -LiteralPath $resolvedStateRoot -PathType Container)) {
  throw 'Hosted-local state does not exist. Run setup.ps1 first.'
}
Assert-HostedLocalExpectedFiles -StateRoot $resolvedStateRoot
Assert-HostedLocalSecretFiles -StateRoot $resolvedStateRoot
$configurationPath = Join-Path $resolvedStateRoot 'config\hosted-local.env'
$environment = Read-HostedLocalEnvironment -Path $configurationPath
Assert-HostedLocalGoogleConfiguration -Environment $environment

$caIdentity = Assert-HostedLocalCertificateFiles -StateRoot $resolvedStateRoot
$trustRecord = Read-HostedLocalTrustRecord -Path (Join-Path $resolvedStateRoot 'trust-record.json')
$store = [Security.Cryptography.X509Certificates.X509Store]::new(
  [Security.Cryptography.X509Certificates.StoreName]::Root,
  [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
try {
  $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
  Assert-HostedLocalCaTrust `
    -Identity $caIdentity `
    -TrustRecord $trustRecord `
    -Certificates @($store.Certificates)
} finally {
  $store.Close()
}

$localhostAddresses = @([Net.Dns]::GetHostAddresses('localhost') | ForEach-Object { $_.ToString() })
Assert-HostedLocalLoopbackAddresses -Addresses $localhostAddresses
$portServices = @{ 8443 = 'api'; 9000 = 'minio' }
foreach ($port in @(8443, 9000)) {
  $available = Test-TcpPortAvailable -Port $port
  $owners = if ($available) { @() } else { @(Get-HostedLocalPortOwnerRecords -Port $port) }
  Assert-HostedLocalPortOwnership `
    -Port $port `
    -Available $available `
    -OwnerRecords $owners `
    -ExpectedService $portServices[$port]
}

$rendered = @(Invoke-HostedLocalCompose `
    -RepositoryRoot $repository `
    -StateRoot $resolvedStateRoot `
    -Arguments @('config', '--format', 'json')) -join "`n"
Assert-HostedLocalComposePolicy -RenderedJson $rendered -StateRoot $resolvedStateRoot

Write-Output 'Firecrawl connections: workspace-managed'

Invoke-HostedLocalCompose `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot `
  -Arguments @('up', '--detach', '--build') | Out-Null

$deadline = [DateTime]::UtcNow.AddMinutes(3)
$health = 'unknown'
do {
  $health = Get-HostedLocalApiHealth `
    -RepositoryRoot $repository `
    -StateRoot $resolvedStateRoot
  if ($health -ceq 'healthy') {
    Write-Output 'Parserium hosted-local is ready at https://localhost:8443/.'
    exit 0
  }
  Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $deadline)

Write-Warning "Hosted-local API did not become healthy within three minutes. Last status: $health"
Invoke-HostedLocalCompose `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot `
  -Arguments @(
    'logs', '--no-color', '--tail', '100',
    'api', 'worker', 'migrate', 'storage-bootstrap', 'db', 'minio'
  )
throw 'Hosted-local startup failed. Existing volumes were preserved.'
