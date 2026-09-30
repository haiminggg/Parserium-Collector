param(
  [Parameter(Mandatory = $true)][string]$ConfirmFingerprint,
  [string]$StateRoot
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'pki.ps1')

function Assert-HostedLocalPkiDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "Hosted-local PKI directory is missing: $Path"
  }
  Assert-NoReparsePointInPath -Path $Path
  $expected = @(
    'ca-key.pem',
    'ca.pem',
    'minio-cert.pem',
    'minio-key.pem',
    'parserium-cert.pem',
    'parserium-key.pem'
  )
  $items = @(Get-ChildItem -LiteralPath $Path -Force)
  if (($items | Where-Object { $_.PSIsContainer }).Count -ne 0) {
    throw 'Hosted-local PKI contains an unexpected directory.'
  }
  if ((@($items.Name | Sort-Object) -join "`n") -cne (($expected | Sort-Object) -join "`n")) {
    throw 'Hosted-local PKI does not contain the exact required files.'
  }
  foreach ($name in $expected) {
    Assert-RegularReadableFile -Path (Join-Path $Path $name) -Label "Hosted-local PKI file $name"
  }

  $ca = Get-HostedLocalCertificateIdentity -CertificatePath (Join-Path $Path 'ca.pem')
  Assert-HostedLocalCertificateAuthority -Identity $ca
  foreach ($leafName in @('parserium-cert.pem', 'minio-cert.pem')) {
    $leaf = Get-HostedLocalCertificateIdentity -CertificatePath (Join-Path $Path $leafName)
    if ($leaf.IsCertificateAuthority) {
      throw "Hosted-local leaf certificate is marked as a CA: $leafName"
    }
    $now = [DateTime]::UtcNow
    if ($leaf.NotBeforeUtc -gt $now -or $leaf.NotAfterUtc -le $now) {
      throw "Hosted-local leaf certificate is outside its validity period: $leafName"
    }
    $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
      $chain.ChainPolicy.RevocationMode = (
        [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
      )
      $chain.ChainPolicy.VerificationFlags = (
        [Security.Cryptography.X509Certificates.X509VerificationFlags]::AllowUnknownCertificateAuthority
      )
      [void]$chain.ChainPolicy.ExtraStore.Add($ca.Certificate)
      if (-not $chain.Build($leaf.Certificate)) {
        throw "Hosted-local leaf certificate did not validate against its CA: $leafName"
      }
      $root = $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
      $rootIdentity = Get-HostedLocalCertificateIdentity -Certificate $root
      if (-not [string]::Equals(
          $rootIdentity.Sha256,
          $ca.Sha256,
          [StringComparison]::Ordinal
        )) {
        throw "Hosted-local leaf certificate resolved to the wrong CA: $leafName"
      }
    } finally {
      $chain.Dispose()
    }
  }
  return $ca
}

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot `
  -StateRoot $resolvedStateRoot `
  -RepositoryRoot $repository
$pkiPath = Join-Path $resolvedStateRoot 'pki'
$currentIdentity = Assert-HostedLocalPkiDirectory -Path $pkiPath
$normalizedConfirmation = $ConfirmFingerprint.Replace(':', '').Replace(' ', '').ToUpperInvariant()
if (
  $normalizedConfirmation -notmatch '^[A-F0-9]{64}$' -or
  -not [string]::Equals(
    $normalizedConfirmation,
    $currentIdentity.Sha256,
    [StringComparison]::Ordinal
  )
) {
  throw 'Certificate rotation confirmation fingerprint does not match the current CA.'
}
$trustRecordPath = Join-Path $resolvedStateRoot 'trust-record.json'
if (Test-Path -LiteralPath $trustRecordPath) {
  throw 'Uninstall the hosted-local CA and remove its trust record before rotation.'
}

$previousStateRoot = $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT
$previousConfigFile = $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE
try {
  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $resolvedStateRoot.Replace('\', '/')
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = (
    Join-Path $resolvedStateRoot 'config\hosted-local.env'
  ).Replace('\', '/')
  $composeArguments = Get-HostedLocalComposeArguments `
    -RepositoryRoot $repository `
    -StateRoot $resolvedStateRoot
  $running = @(& docker compose @composeArguments ps --status running --quiet)
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not determine hosted-local container status.'
  }
} finally {
  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $previousStateRoot
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = $previousConfigFile
}
if ($running.Count -ne 0) {
  throw 'Stop every hosted-local container before rotating certificates.'
}

$store = [Security.Cryptography.X509Certificates.X509Store]::new(
  [Security.Cryptography.X509Certificates.StoreName]::Root,
  [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
try {
  $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
  $installedMatches = @(
    foreach ($certificate in $store.Certificates) {
      $candidate = Get-HostedLocalCertificateIdentity -Certificate $certificate
      if ([string]::Equals(
          $candidate.Sha256,
          $currentIdentity.Sha256,
          [StringComparison]::Ordinal
        )) {
        $certificate
      }
    }
  )
} finally {
  $store.Close()
}
if ($installedMatches.Count -ne 0) {
  throw 'Uninstall the current hosted-local CA before rotating certificates.'
}

$staging = Join-Path $resolvedStateRoot ('pki-staging-' + [Guid]::NewGuid().ToString('N'))
$archiveRoot = Join-Path $resolvedStateRoot 'pki-archive'
$archiveTarget = Join-Path $archiveRoot $currentIdentity.Sha256
$oldPkiArchived = $false
try {
  New-Item -ItemType Directory -Path $staging | Out-Null
  $stateParent = Split-Path -Parent $resolvedStateRoot
  $stateName = Split-Path -Leaf $resolvedStateRoot
  $mount = "${stateParent}:/host-state"
  $containerOutput = "/host-state/${stateName}/$([IO.Path]::GetFileName($staging))"
  & docker run --rm --volume $mount `
    parserium-collector:hosted-local `
    python -m parserium_collector.cli.development_pki $containerOutput | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw 'Hosted-local replacement PKI generation failed.'
  }
  Set-HostedLocalPkiContainerOwner `
    -StateRoot $resolvedStateRoot `
    -PkiDirectoryName ([IO.Path]::GetFileName($staging))
  $replacementIdentity = Assert-HostedLocalPkiDirectory -Path $staging
  if ([string]::Equals(
      $replacementIdentity.Sha256,
      $currentIdentity.Sha256,
      [StringComparison]::Ordinal
    )) {
    throw 'Replacement hosted-local CA unexpectedly reused the old fingerprint.'
  }

  if (Test-Path -LiteralPath $archiveTarget) {
    throw "Certificate archive target already exists: $archiveTarget"
  }
  if (Test-Path -LiteralPath $archiveRoot) {
    $archiveItem = Get-Item -LiteralPath $archiveRoot -Force
    if (-not $archiveItem.PSIsContainer -or ($archiveItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Certificate archive path has an unsafe collision: $archiveRoot"
    }
  } else {
    New-Item -ItemType Directory -Path $archiveRoot | Out-Null
  }
  Assert-NoReparsePointInPath -Path $archiveRoot
  Move-Item -LiteralPath $pkiPath -Destination $archiveTarget
  $oldPkiArchived = $true
  try {
    Move-Item -LiteralPath $staging -Destination $pkiPath
  } catch {
    Move-Item -LiteralPath $archiveTarget -Destination $pkiPath
    $oldPkiArchived = $false
    throw 'Replacement PKI activation failed; the prior PKI was restored.'
  }

  Protect-HostedLocalPath -Path (Join-Path $pkiPath 'ca-key.pem')
  Protect-HostedLocalPath -Path (Join-Path $pkiPath 'parserium-key.pem')
  Protect-HostedLocalPath -Path (Join-Path $pkiPath 'minio-key.pem')
  Write-Output "Archived prior PKI at: $archiveTarget"
  Write-Output "New hosted-local CA SHA-256: $($replacementIdentity.Sha256)"
} finally {
  if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
  }
  if (-not $oldPkiArchived -and (Test-Path -LiteralPath $archiveTarget)) {
    throw "Certificate rotation left an unexpected archive state: $archiveTarget"
  }
}
