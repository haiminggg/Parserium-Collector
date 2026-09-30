param([string]$BackendTestImage = 'parserium-collector:hosted-local')

$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$pkiLibrary = Join-Path $repository 'scripts\hosted-local\pki.ps1'
if (-not (Test-Path -LiteralPath $pkiLibrary -PathType Leaf)) {
  throw "Hosted-local PKI library is missing: $pkiLibrary"
}
foreach ($scriptName in @('install-ca.ps1', 'uninstall-ca.ps1', 'rotate-certificates.ps1')) {
  $scriptPath = Join-Path $repository "scripts\hosted-local\$scriptName"
  if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Hosted-local PKI command is missing: $scriptPath"
  }
}
. (Join-Path $repository 'scripts\hosted-local\common.ps1')
. $pkiLibrary
. (Join-Path $repository 'scripts\hosted-local\lifecycle.ps1')

$testRoot = [IO.Path]::GetFullPath(
  (Join-Path ([IO.Path]::GetTempPath()) ('parserium-hosted-local-pki-' + [Guid]::NewGuid().ToString('N')))
)

function Assert-AuthorityRejected {
  param(
    [Parameter(Mandatory = $true)][object]$Identity,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $failure = $null
  try {
    Assert-HostedLocalCertificateAuthority -Identity $Identity
  } catch {
    $failure = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($failure)) {
    throw "Invalid certificate authority identity was accepted: $Label"
  }
}

function Copy-Identity {
  param([Parameter(Mandatory = $true)][object]$Identity)

  return [PSCustomObject]@{
    Subject = $Identity.Subject
    SerialNumber = $Identity.SerialNumber
    Sha256 = $Identity.Sha256
    NotBeforeUtc = $Identity.NotBeforeUtc
    NotAfterUtc = $Identity.NotAfterUtc
    IsCertificateAuthority = $Identity.IsCertificateAuthority
    Certificate = $Identity.Certificate
  }
}

try {
  New-Item -ItemType Directory -Path $testRoot | Out-Null
  $output = Join-Path $testRoot 'pki'
  New-Item -ItemType Directory -Path $output | Out-Null
  $parent = Split-Path -Parent $testRoot
  $name = Split-Path -Leaf $testRoot
  $mount = "${parent}:/host-test"
  & docker run --rm --volume $mount `
    $BackendTestImage `
    /app/.venv/bin/python -m parserium_collector.cli.development_pki `
    "/host-test/$name/pki" | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not generate the hosted-local PKI test fixture.'
  }

  $caPath = Join-Path $output 'ca.pem'
  $identity = Get-HostedLocalCertificateIdentity -CertificatePath $caPath
  if ($identity.Subject -ne 'CN=Parserium hosted-local development CA') {
    throw 'Wrong CA subject.'
  }
  if ($identity.Sha256 -notmatch '^[A-F0-9]{64}$') {
    throw 'Wrong SHA-256 fingerprint.'
  }
  if (-not $identity.IsCertificateAuthority) {
    throw 'CA constraint was not detected.'
  }
  Assert-HostedLocalCertificateAuthority -Identity $identity
  $validatedCa = Assert-HostedLocalCertificateFiles -StateRoot $testRoot
  if ($validatedCa.Sha256 -cne $identity.Sha256) {
    throw 'Lifecycle certificate validation returned the wrong CA identity.'
  }

  $trustRecordPath = Join-Path $testRoot 'trust-record.json'
  $record = [ordered]@{
    store = 'CurrentUser/Root'
    subject = $identity.Subject
    serial_number = $identity.SerialNumber
    sha256 = $identity.Sha256
  }
  Write-AtomicUtf8File -Path $trustRecordPath -Value ($record | ConvertTo-Json)
  $loadedRecord = Read-HostedLocalTrustRecord -Path $trustRecordPath
  if (-not (Test-HostedLocalTrustRecordMatch -Record $loadedRecord -Identity $identity)) {
    throw 'Exact hosted-local trust record did not match the CA.'
  }

  $certificateStoreTestDouble = @($identity.Certificate)
  if (-not (Test-HostedLocalCaInstalled `
      -Identity $identity `
      -Certificates $certificateStoreTestDouble)) {
    throw 'Exact CA was not found in the certificate-store test double.'
  }
  $leafIdentity = Get-HostedLocalCertificateIdentity `
    -CertificatePath (Join-Path $output 'parserium-cert.pem')
  if (Test-HostedLocalCaInstalled -Identity $identity -Certificates @($leafIdentity.Certificate)) {
    throw 'A leaf certificate was accepted as the installed hosted-local CA.'
  }

  foreach ($case in @(
    @{ Name = 'subject'; Property = 'subject'; Value = 'CN=Unexpected CA' },
    @{ Name = 'serial'; Property = 'serial_number'; Value = '00DEADBEEF' },
    @{ Name = 'sha256'; Property = 'sha256'; Value = ('0' * 64) },
    @{ Name = 'store'; Property = 'store'; Value = 'CurrentUser/My' }
  )) {
    $mismatch = [PSCustomObject]@{
      store = $record.store
      subject = $record.subject
      serial_number = $record.serial_number
      sha256 = $record.sha256
    }
    $mismatch.($case.Property) = $case.Value
    if (Test-HostedLocalTrustRecordMatch -Record $mismatch -Identity $identity) {
      throw "Trust-record mismatch was accepted: $($case.Name)"
    }
  }

  $wrongSubject = Copy-Identity -Identity $identity
  $wrongSubject.Subject = 'CN=Unexpected CA'
  Assert-AuthorityRejected -Identity $wrongSubject -Label 'subject'

  $expired = Copy-Identity -Identity $identity
  $expired.NotAfterUtc = [DateTime]::UtcNow.AddMinutes(-1)
  Assert-AuthorityRejected -Identity $expired -Label 'expiry'
  Assert-AuthorityRejected -Identity $leafIdentity -Label 'leaf certificate'

  $librarySource = [IO.File]::ReadAllText($pkiLibrary)
  if ($librarySource -match 'Cert:\\|X509Store') {
    throw 'Pure PKI helpers contain certificate-store access.'
  }
  foreach ($scriptName in @('setup.ps1', 'rotate-certificates.ps1')) {
    $scriptSource = [IO.File]::ReadAllText(
      (Join-Path $repository "scripts\hosted-local\$scriptName")
    )
    $normalizationCalls = [regex]::Matches(
      $scriptSource,
      'Set-HostedLocalPkiContainerOwner'
    ).Count
    if ($normalizationCalls -ne 1) {
      throw "$scriptName must normalize generated PKI container ownership exactly once."
    }
  }

  Write-Output 'PASS: hosted-local PKI identity checks use only the certificate-store test double.'
} finally {
  if (Test-Path -LiteralPath $testRoot) {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove an unexpected PKI test path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
