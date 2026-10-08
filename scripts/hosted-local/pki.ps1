function ConvertTo-HostedLocalCertificateIdentity {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
  )

  $hasher = [Security.Cryptography.SHA256]::Create()
  try {
    $sha256 = [BitConverter]::ToString($hasher.ComputeHash($Certificate.RawData)).Replace('-', '')
  } finally {
    $hasher.Dispose()
  }

  $isCertificateAuthority = $false
  foreach ($extension in $Certificate.Extensions) {
    if ($extension.Oid.Value -ceq '2.5.29.19') {
      $constraints = [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new(
        $extension,
        $extension.Critical
      )
      $isCertificateAuthority = $constraints.CertificateAuthority
      break
    }
  }

  return [PSCustomObject]@{
    Subject = $Certificate.Subject
    SerialNumber = $Certificate.SerialNumber
    Sha256 = $sha256.ToUpperInvariant()
    NotBeforeUtc = $Certificate.NotBefore.ToUniversalTime()
    NotAfterUtc = $Certificate.NotAfter.ToUniversalTime()
    IsCertificateAuthority = $isCertificateAuthority
    Certificate = $Certificate
  }
}

function Get-HostedLocalCertificateIdentity {
  [CmdletBinding(DefaultParameterSetName = 'Path')]
  param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Path')][string]$CertificatePath,
    [Parameter(Mandatory = $true, ParameterSetName = 'Certificate')]
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
  )

  if ($PSCmdlet.ParameterSetName -eq 'Path') {
    if (-not (Test-Path -LiteralPath $CertificatePath)) {
      throw "Certificate does not exist: $CertificatePath"
    }
    $item = Get-Item -LiteralPath $CertificatePath -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Certificate path is not a regular file: $CertificatePath"
    }
    try {
      $Certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        [IO.File]::ReadAllBytes($item.FullName)
      )
    } catch {
      throw "Certificate file could not be parsed: $CertificatePath"
    }
  }
  return ConvertTo-HostedLocalCertificateIdentity -Certificate $Certificate
}

function Assert-HostedLocalCertificateAuthority {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][object]$Identity)

  $expectedSubject = 'CN=Parserium hosted-local development CA'
  if (-not [string]::Equals(
      [string]$Identity.Subject,
      $expectedSubject,
      [StringComparison]::Ordinal
    )) {
    throw 'Hosted-local certificate authority has an unexpected subject.'
  }
  if ([string]$Identity.SerialNumber -notmatch '^[A-Fa-f0-9]+$') {
    throw 'Hosted-local certificate authority has an invalid serial number.'
  }
  if ([string]$Identity.Sha256 -notmatch '^[A-F0-9]{64}$') {
    throw 'Hosted-local certificate authority has an invalid SHA-256 fingerprint.'
  }
  if ($Identity.IsCertificateAuthority -ne $true) {
    throw 'Hosted-local certificate is not a certificate authority.'
  }
  $now = [DateTime]::UtcNow
  if ([DateTime]$Identity.NotBeforeUtc -gt $now) {
    throw 'Hosted-local certificate authority is not valid yet.'
  }
  if ([DateTime]$Identity.NotAfterUtc -le $now) {
    throw 'Hosted-local certificate authority is expired.'
  }
}

function Read-HostedLocalTrustRecord {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Hosted-local trust record does not exist: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Hosted-local trust record is not a regular file: $Path"
  }
  try {
    $record = [IO.File]::ReadAllText($item.FullName) | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw 'Hosted-local trust record could not be parsed.'
  }
  $properties = @($record.PSObject.Properties.Name | Sort-Object)
  $expected = @('serial_number', 'sha256', 'store', 'subject')
  if (($properties -join "`n") -cne (($expected | Sort-Object) -join "`n")) {
    throw 'Hosted-local trust record has unexpected fields.'
  }
  if (
    $record.store -isnot [string] -or
    $record.subject -isnot [string] -or
    $record.serial_number -isnot [string] -or
    $record.sha256 -isnot [string] -or
    [string]::IsNullOrWhiteSpace($record.store) -or
    [string]::IsNullOrWhiteSpace($record.subject) -or
    $record.serial_number -notmatch '^[A-Fa-f0-9]+$' -or
    $record.sha256 -notmatch '^[A-F0-9]{64}$'
  ) {
    throw 'Hosted-local trust record has invalid values.'
  }
  return $record
}

function Test-HostedLocalTrustRecordMatch {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$Record,
    [Parameter(Mandatory = $true)][object]$Identity,
    [string]$StoreName = 'CurrentUser/Root'
  )

  try {
    Assert-HostedLocalCertificateAuthority -Identity $Identity
  } catch {
    return $false
  }
  return (
    [string]::Equals([string]$Record.store, $StoreName, [StringComparison]::Ordinal) -and
    [string]::Equals(
      [string]$Record.subject,
      [string]$Identity.Subject,
      [StringComparison]::Ordinal
    ) -and
    [string]::Equals(
      [string]$Record.serial_number,
      [string]$Identity.SerialNumber,
      [StringComparison]::Ordinal
    ) -and
    [string]::Equals(
      [string]$Record.sha256,
      [string]$Identity.Sha256,
      [StringComparison]::Ordinal
    )
  )
}

function Test-HostedLocalCaInstalled {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$Identity,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Certificates,
    [string]$StoreName = 'CurrentUser/Root'
  )

  if (-not [string]::Equals(
      $StoreName,
      'CurrentUser/Root',
      [StringComparison]::Ordinal
    )) {
    return $false
  }
  try {
    Assert-HostedLocalCertificateAuthority -Identity $Identity
  } catch {
    return $false
  }
  $matches = @(
    foreach ($certificate in $Certificates) {
      if ($certificate -isnot [Security.Cryptography.X509Certificates.X509Certificate2]) {
        continue
      }
      $candidate = Get-HostedLocalCertificateIdentity -Certificate $certificate
      try {
        Assert-HostedLocalCertificateAuthority -Identity $candidate
      } catch {
        continue
      }
      if (
        [string]::Equals(
          [string]$candidate.Subject,
          [string]$Identity.Subject,
          [StringComparison]::Ordinal
        ) -and
        [string]::Equals(
          [string]$candidate.SerialNumber,
          [string]$Identity.SerialNumber,
          [StringComparison]::Ordinal
        ) -and
        [string]::Equals(
          [string]$candidate.Sha256,
          [string]$Identity.Sha256,
          [StringComparison]::Ordinal
        )
      ) {
        $candidate
      }
    }
  )
  return $matches.Count -eq 1
}
