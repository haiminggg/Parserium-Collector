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

if (Test-Path -LiteralPath $trustRecordPath) {
  $existingRecord = Read-HostedLocalTrustRecord -Path $trustRecordPath
  if (-not (Test-HostedLocalTrustRecordMatch -Record $existingRecord -Identity $fileIdentity)) {
    throw 'Existing hosted-local trust record does not match the current CA.'
  }
}

function Get-MatchingHostedLocalCertificates {
  param(
    [Parameter(Mandatory = $true)][object[]]$Certificates,
    [Parameter(Mandatory = $true)][string]$Sha256
  )

  return @(
    foreach ($certificate in $Certificates) {
      $candidate = Get-HostedLocalCertificateIdentity -Certificate $certificate
      if ([string]::Equals($candidate.Sha256, $Sha256, [StringComparison]::Ordinal)) {
        $certificate
      }
    }
  )
}

Write-Output "Hosted-local CA SHA-256: $($fileIdentity.Sha256)"
$store = [Security.Cryptography.X509Certificates.X509Store]::new(
  [Security.Cryptography.X509Certificates.StoreName]::Root,
  [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
$addedByThisRun = $false
try {
  $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
  $matches = @(Get-MatchingHostedLocalCertificates `
    -Certificates @($store.Certificates) `
    -Sha256 $fileIdentity.Sha256)
  if ($matches.Count -gt 1) {
    throw 'CurrentUser root store contains an ambiguous hosted-local CA match.'
  }
  if ($matches.Count -eq 0) {
    $store.Add($fileIdentity.Certificate)
    $addedByThisRun = $true
  }
} finally {
  $store.Close()
}

try {
  $runtimeIdentity = $null
  try {
    $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $installed = @(Get-MatchingHostedLocalCertificates `
      -Certificates @($store.Certificates) `
      -Sha256 $fileIdentity.Sha256)
    if ($installed.Count -ne 1) {
      throw 'Installed hosted-local CA could not be resolved uniquely.'
    }
    $runtimeIdentity = Get-HostedLocalCertificateIdentity -Certificate $installed[0]
    Assert-HostedLocalCertificateAuthority -Identity $runtimeIdentity
  } finally {
    $store.Close()
  }

  $record = [ordered]@{
    store = 'CurrentUser/Root'
    subject = $runtimeIdentity.Subject
    serial_number = $runtimeIdentity.SerialNumber
    sha256 = $runtimeIdentity.Sha256
  }
  $recordJson = $record | ConvertTo-Json
  Write-AtomicUtf8File -Path $trustRecordPath -Value $recordJson
  Protect-HostedLocalPath -Path $trustRecordPath
} catch {
  if ($addedByThisRun) {
    try {
      $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      $rollbackMatches = @(Get-MatchingHostedLocalCertificates `
        -Certificates @($store.Certificates) `
        -Sha256 $fileIdentity.Sha256)
      if ($rollbackMatches.Count -eq 1) {
        $store.Remove($rollbackMatches[0])
      }
    } finally {
      $store.Close()
    }
  }
  throw 'Hosted-local CA installation could not be recorded and was rolled back.'
}

Write-Output 'Installed the exact hosted-local CA in CurrentUser/Root.'
