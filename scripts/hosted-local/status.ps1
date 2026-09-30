param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'pki.ps1')
. (Join-Path $PSScriptRoot 'lifecycle.ps1')

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot -StateRoot $resolvedStateRoot -RepositoryRoot $repository
$configurationPath = Join-Path $resolvedStateRoot 'config\hosted-local.env'
$environment = Read-HostedLocalEnvironment -Path $configurationPath

$caPath = Join-Path $resolvedStateRoot 'pki\ca.pem'
if (Test-Path -LiteralPath $caPath -PathType Leaf) {
  try {
    $caIdentity = Get-HostedLocalCertificateIdentity -CertificatePath $caPath
    Write-Output "CA expires (UTC): $($caIdentity.NotAfterUtc.ToString('u'))"
  } catch {
    Write-Output 'CA expires (UTC): invalid certificate'
  }
} else {
  Write-Output 'CA expires (UTC): missing'
}

$googleConfigured = 'no'
try {
  Assert-HostedLocalGoogleConfiguration -Environment $environment
  if (Test-Path -LiteralPath (Join-Path $resolvedStateRoot 'secrets\google_client_secret') -PathType Leaf) {
    $googleConfigured = 'yes'
  }
} catch {
  $googleConfigured = 'no'
}
Write-Output "Google configured: $googleConfigured"

Write-Output 'Firecrawl connections: workspace-managed'

$portServices = @{ 8443 = 'api'; 9000 = 'minio' }
foreach ($port in @(8443, 9000)) {
  if (Test-TcpPortAvailable -Port $port) {
    Write-Output "Port ${port}: free"
    continue
  }
  try {
    $owners = @(Get-HostedLocalPortOwnerRecords -Port $port)
    Assert-HostedLocalPortOwnership `
      -Port $port `
      -Available $false `
      -OwnerRecords $owners `
      -ExpectedService $portServices[$port]
    Write-Output "Port ${port}: parserium-hosted-local/$($portServices[$port])"
  } catch {
    Write-Output "Port ${port}: occupied outside the expected hosted-local container"
  }
}

Write-Output 'Containers:'
Invoke-HostedLocalCompose `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot `
  -Arguments @('ps')
