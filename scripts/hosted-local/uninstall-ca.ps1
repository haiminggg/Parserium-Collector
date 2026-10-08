param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'pki.ps1')

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot `
  -StateRoot $resolvedStateRoot `
  -RepositoryRoot $repository
$caPath = Join-Path $resolvedStateRoot 'pki\ca.pem'
$trustRecordPath = Join-Path $resolvedStateRoot 'trust-record.json'
$fileIdentity = Get-HostedLocalCertificateIdentity -CertificatePath $caPath
Assert-HostedLocalCertificateAuthority -Identity $fileIdentity
$record = Read-HostedLocalTrustRecord -Path $trustRecordPath
if (-not (Test-HostedLocalTrustRecordMatch -Record $record -Identity $fileIdentity)) {
  throw 'Hosted-local trust record does not match the current CA file.'
}

function Get-RecordMatchingCertificates {
  param(
    [Parameter(Mandatory = $true)][object[]]$Certificates,
    [Parameter(Mandatory = $true)][object]$TrustRecord
  )

  return @(
    foreach ($certificate in $Certificates) {
      $candidate = Get-HostedLocalCertificateIdentity -Certificate $certificate
      if (Test-HostedLocalTrustRecordMatch -Record $TrustRecord -Identity $candidate) {
        $certificate
      }
    }
  )
}

$store = [Security.Cryptography.X509Certificates.X509Store]::new(
  [Security.Cryptography.X509Certificates.StoreName]::Root,
  [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
try {
  $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
  $matches = @(Get-RecordMatchingCertificates `
    -Certificates @($store.Certificates) `
    -TrustRecord $record)
  if ($matches.Count -ne 1) {
    throw 'CurrentUser root store did not contain exactly one trust-record match.'
  }
  $store.Remove($matches[0])
} finally {
  $store.Close()
}

try {
  $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
  $remaining = @(Get-RecordMatchingCertificates `
    -Certificates @($store.Certificates) `
    -TrustRecord $record)
  if ($remaining.Count -ne 0) {
    throw 'Hosted-local CA remained installed after removal.'
  }
} finally {
  $store.Close()
}

Remove-Item -LiteralPath $trustRecordPath -Force
Write-Output "Removed hosted-local CA SHA-256: $($record.sha256)"
Write-Output 'Removed only the fingerprint-recorded certificate from CurrentUser/Root.'
