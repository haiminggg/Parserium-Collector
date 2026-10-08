function Read-HostedLocalEnvironment {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  Assert-RegularReadableFile -Path $Path -Label 'Hosted-local configuration'
  $environment = @{}
  foreach ($line in [IO.File]::ReadAllLines([IO.Path]::GetFullPath($Path))) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
      continue
    }
    $separator = $line.IndexOf('=')
    if ($separator -le 0) {
      throw 'Hosted-local configuration contains an invalid line.'
    }
    $name = $line.Substring(0, $separator)
    $value = $line.Substring($separator + 1)
    if ($name -notmatch '^[A-Z][A-Z0-9_]*$') {
      throw "Hosted-local configuration contains an invalid setting name: $name"
    }
    if (-not $environment.ContainsKey($name)) {
      $environment[$name] = @()
    }
    $environment[$name] = @($environment[$name]) + @($value)
  }
  return $environment
}

function Assert-HostedLocalExpectedFiles {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$StateRoot)

  $expected = @(
    @{ Path = 'config\hosted-local.env'; Label = 'Hosted-local configuration' },
    @{ Path = 'secrets\db_password'; Label = 'Database secret' },
    @{ Path = 'secrets\session_signing_secret'; Label = 'Session signing secret' },
    @{ Path = 'secrets\google_client_secret'; Label = 'Google client secret' },
    @{ Path = 'secrets\minio_access_key'; Label = 'MinIO access key' },
    @{ Path = 'secrets\minio_secret_key'; Label = 'MinIO secret key' },
    @{ Path = 'secrets\firecrawl_credential_wrapping_key'; Label = 'Firecrawl credential wrapping key' },
    @{ Path = 'secrets\discovery_fingerprint_secret'; Label = 'Discovery fingerprint secret' },
    @{ Path = 'pki\ca-key.pem'; Label = 'CA private key' },
    @{ Path = 'pki\ca.pem'; Label = 'CA certificate' },
    @{ Path = 'pki\parserium-key.pem'; Label = 'Parserium private key' },
    @{ Path = 'pki\parserium-cert.pem'; Label = 'Parserium certificate' },
    @{ Path = 'pki\minio-key.pem'; Label = 'MinIO private key' },
    @{ Path = 'pki\minio-cert.pem'; Label = 'MinIO certificate' },
    @{ Path = 'trust-record.json'; Label = 'Hosted-local trust record' }
  )
  foreach ($entry in $expected) {
    Assert-RegularReadableFile `
      -Path (Join-Path $StateRoot $entry.Path) `
      -Label $entry.Label
  }
}

function Assert-HostedLocalSecretFiles {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$StateRoot)

  $requirements = @(
    @{ Path = 'secrets\db_password'; Label = 'Database'; Pattern = '^[A-Za-z0-9_-]{64}$' },
    @{ Path = 'secrets\session_signing_secret'; Label = 'Session signing'; Pattern = '^[A-Za-z0-9_-]{64}$' },
    @{ Path = 'secrets\google_client_secret'; Label = 'Google client'; Pattern = '^[^\r\n\0]{8,}$' },
    @{ Path = 'secrets\minio_access_key'; Label = 'MinIO access key'; Pattern = '^parserium-[A-Za-z0-9_-]{32}$' },
    @{ Path = 'secrets\minio_secret_key'; Label = 'MinIO secret key'; Pattern = '^[A-Za-z0-9_-]{64}$' }
    @{ Path = 'secrets\discovery_fingerprint_secret'; Label = 'Discovery fingerprint'; Pattern = '^[A-Za-z0-9_-]{64}$' }
  )
  foreach ($requirement in $requirements) {
    $path = Join-Path $StateRoot $requirement.Path
    Assert-RegularReadableFile -Path $path -Label "$($requirement.Label) secret"
    $value = [IO.File]::ReadAllText($path)
    if ($value -notmatch $requirement.Pattern) {
      throw "$($requirement.Label) secret does not satisfy the required format or length."
    }
  }
  Assert-HostedLocalCredentialWrappingKey `
    -Path (Join-Path $StateRoot 'secrets\firecrawl_credential_wrapping_key')
}

function Assert-HostedLocalGoogleConfiguration {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][hashtable]$Environment)

  $values = if ($Environment.ContainsKey('DASHBOARD_OIDC_CLIENT_ID')) {
    @($Environment['DASHBOARD_OIDC_CLIENT_ID'])
  } else {
    @()
  }
  if (
    $values.Count -ne 1 -or
    [string]::IsNullOrWhiteSpace([string]$values[0]) -or
    [string]$values[0] -ne ([string]$values[0]).Trim() -or
    [string]$values[0] -match '[\r\n\0]'
  ) {
    throw 'Google client ID must be configured exactly once.'
  }
}

function Assert-HostedLocalCertificateWindow {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$Identity,
    [Parameter(Mandatory = $true)][string]$Label,
    [switch]$RequireCa
  )

  $now = [DateTime]::UtcNow
  if ([DateTime]$Identity.NotBeforeUtc -gt $now) {
    throw "$Label certificate is not valid yet."
  }
  if ([DateTime]$Identity.NotAfterUtc -le $now) {
    throw "$Label certificate is expired."
  }
  if ($RequireCa -and $Identity.IsCertificateAuthority -ne $true) {
    throw "$Label certificate is not a certificate authority."
  }
  if (-not $RequireCa -and $Identity.IsCertificateAuthority -eq $true) {
    throw "$Label certificate must be a leaf certificate."
  }
}

function Read-HostedLocalDerLength {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][byte[]]$Data,
    [Parameter(Mandatory = $true)][int]$Offset
  )

  if ($Offset -ge $Data.Length) {
    throw 'Certificate SAN contains a truncated DER length.'
  }
  $first = [int]$Data[$Offset]
  if (($first -band 0x80) -eq 0) {
    return [PSCustomObject]@{ Length = $first; NextOffset = $Offset + 1 }
  }
  $count = $first -band 0x7F
  if ($count -lt 1 -or $count -gt 4 -or ($Offset + $count) -ge $Data.Length) {
    throw 'Certificate SAN contains an invalid DER length.'
  }
  $length = 0
  for ($index = 1; $index -le $count; $index++) {
    $length = ($length -shl 8) -bor [int]$Data[$Offset + $index]
  }
  return [PSCustomObject]@{ Length = $length; NextOffset = $Offset + $count + 1 }
}

function Get-HostedLocalSubjectAlternativeNames {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
  )

  $extension = @($Certificate.Extensions | Where-Object { $_.Oid.Value -ceq '2.5.29.17' })
  if ($extension.Count -ne 1) {
    throw 'Certificate must contain exactly one subject alternative name extension.'
  }
  $data = [byte[]]$extension[0].RawData
  if ($data.Length -lt 2 -or $data[0] -ne 0x30) {
    throw 'Certificate SAN has an invalid DER sequence.'
  }
  $sequenceLength = Read-HostedLocalDerLength -Data $data -Offset 1
  $offset = [int]$sequenceLength.NextOffset
  $end = $offset + [int]$sequenceLength.Length
  if ($end -ne $data.Length) {
    throw 'Certificate SAN has an invalid DER boundary.'
  }
  $dnsNames = [Collections.Generic.List[string]]::new()
  $ipAddresses = [Collections.Generic.List[string]]::new()
  while ($offset -lt $end) {
    $tag = [int]$data[$offset]
    $valueLength = Read-HostedLocalDerLength -Data $data -Offset ($offset + 1)
    $valueOffset = [int]$valueLength.NextOffset
    $nextOffset = $valueOffset + [int]$valueLength.Length
    if ($nextOffset -gt $end) {
      throw 'Certificate SAN contains a truncated value.'
    }
    $value = [byte[]]$data[$valueOffset..($nextOffset - 1)]
    if ($tag -eq 0x82) {
      $dnsNames.Add([Text.Encoding]::ASCII.GetString($value))
    } elseif ($tag -eq 0x87) {
      if ($value.Length -ne 4 -and $value.Length -ne 16) {
        throw 'Certificate SAN contains an invalid IP address.'
      }
      $ipAddresses.Add(([Net.IPAddress]::new($value)).ToString())
    }
    $offset = $nextOffset
  }
  return [PSCustomObject]@{
    DnsNames = @($dnsNames)
    IpAddresses = @($ipAddresses)
  }
}

function Assert-HostedLocalLeafCertificate {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$Identity,
    [Parameter(Mandatory = $true)][object]$CaIdentity,
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string[]]$RequiredDnsNames,
    [Parameter(Mandatory = $true)][string[]]$RequiredIpAddresses
  )

  Assert-HostedLocalCertificateWindow -Identity $Identity -Label $Label
  if (-not [string]::Equals(
      [string]$Identity.Certificate.Issuer,
      [string]$CaIdentity.Certificate.Subject,
      [StringComparison]::Ordinal
    )) {
    throw "$Label certificate issuer does not match the hosted-local CA."
  }

  $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
  try {
    $chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
    $chain.ChainPolicy.VerificationFlags = (
      [Security.Cryptography.X509Certificates.X509VerificationFlags]::AllowUnknownCertificateAuthority
    )
    [void]$chain.ChainPolicy.ExtraStore.Add($CaIdentity.Certificate)
    [void]$chain.ChainPolicy.ApplicationPolicy.Add(
      [Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1')
    )
    if (-not $chain.Build($Identity.Certificate)) {
      throw "$Label certificate chain validation failed."
    }
    $elements = @($chain.ChainElements)
    if ($elements.Count -ne 2) {
      throw "$Label certificate chain does not terminate directly at the hosted-local CA."
    }
    $rootIdentity = Get-HostedLocalCertificateIdentity -Certificate $elements[-1].Certificate
    if (-not [string]::Equals(
        [string]$rootIdentity.Sha256,
        [string]$CaIdentity.Sha256,
        [StringComparison]::Ordinal
      )) {
      throw "$Label certificate chain terminates at an unexpected authority."
    }
  } finally {
    $chain.Dispose()
  }

  $names = Get-HostedLocalSubjectAlternativeNames -Certificate $Identity.Certificate
  foreach ($required in $RequiredDnsNames) {
    if (@($names.DnsNames | Where-Object { $_ -ceq $required }).Count -ne 1) {
      throw "$Label certificate is missing required DNS SAN $required."
    }
  }
  foreach ($required in $RequiredIpAddresses) {
    if (@($names.IpAddresses | Where-Object { $_ -ceq $required }).Count -ne 1) {
      throw "$Label certificate is missing required IP SAN $required."
    }
  }
}

function Assert-HostedLocalCertificateFiles {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$StateRoot)

  $ca = Get-HostedLocalCertificateIdentity -CertificatePath (Join-Path $StateRoot 'pki\ca.pem')
  Assert-HostedLocalCertificateAuthority -Identity $ca
  Assert-HostedLocalCertificateWindow -Identity $ca -Label 'CA' -RequireCa
  $parserium = Get-HostedLocalCertificateIdentity `
    -CertificatePath (Join-Path $StateRoot 'pki\parserium-cert.pem')
  Assert-HostedLocalLeafCertificate `
    -Identity $parserium `
    -CaIdentity $ca `
    -Label 'Parserium' `
    -RequiredDnsNames @('localhost') `
    -RequiredIpAddresses @('127.0.0.1')
  $minio = Get-HostedLocalCertificateIdentity `
    -CertificatePath (Join-Path $StateRoot 'pki\minio-cert.pem')
  Assert-HostedLocalLeafCertificate `
    -Identity $minio `
    -CaIdentity $ca `
    -Label 'MinIO' `
    -RequiredDnsNames @('minio', 'localhost') `
    -RequiredIpAddresses @('127.0.0.1')
  return $ca
}

function Assert-HostedLocalCaTrust {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][object]$Identity,
    [Parameter(Mandatory = $true)][object]$TrustRecord,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Certificates
  )

  if (-not (Test-HostedLocalTrustRecordMatch -Record $TrustRecord -Identity $Identity)) {
    throw 'Hosted-local trust record does not match the active CA.'
  }
  if (-not (Test-HostedLocalCaInstalled -Identity $Identity -Certificates $Certificates)) {
    throw 'The exact hosted-local CA is not installed exactly once in CurrentUser/Root.'
  }
}

function Assert-HostedLocalLoopbackAddresses {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string[]]$Addresses)

  if ($Addresses.Count -eq 0) {
    throw 'localhost did not resolve to an address.'
  }
  foreach ($addressText in $Addresses) {
    $address = $null
    if (-not [Net.IPAddress]::TryParse($addressText, [ref]$address)) {
      throw "localhost resolved to an invalid address: $addressText"
    }
    if (
      -not $address.Equals([Net.IPAddress]::Loopback) -and
      -not $address.Equals([Net.IPAddress]::IPv6Loopback)
    ) {
      throw "localhost resolved to a non-loopback address: $addressText"
    }
  }
}

function Assert-HostedLocalPortOwnership {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$Port,
    [Parameter(Mandatory = $true)][bool]$Available,
    [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$OwnerRecords,
    [Parameter(Mandatory = $true)][string]$ExpectedService
  )

  if ($Available) {
    return
  }
  $valid = @(
    $OwnerRecords | Where-Object {
      $_.Project -ceq 'parserium-hosted-local' -and $_.Service -ceq $ExpectedService
    }
  )
  if ($OwnerRecords.Count -ne 1 -or $valid.Count -ne 1) {
    throw "Port $Port is occupied by a process outside the exact hosted-local $ExpectedService container."
  }
}

function Get-HostedLocalPortOwnerRecords {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$Port)

  $containerIds = @(& docker ps --filter "publish=$Port" --format '{{.ID}}')
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect Docker ownership for port $Port."
  }
  $records = @()
  foreach ($containerId in $containerIds) {
    if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
      continue
    }
    $labelJson = @(& docker inspect `
        --format '{{json .Config.Labels}}' `
        ([string]$containerId).Trim())
    if ($LASTEXITCODE -ne 0 -or $labelJson.Count -ne 1) {
      throw "Could not inspect the container using port $Port."
    }
    try {
      $labels = ([string]$labelJson[0]) | ConvertFrom-Json -ErrorAction Stop
    } catch {
      throw "Could not parse container labels for port $Port."
    }
    $records += [PSCustomObject]@{
      Project = [string]$labels.'com.docker.compose.project'
      Service = [string]$labels.'com.docker.compose.service'
    }
  }
  return @($records)
}

function Get-HostedLocalFirecrawlStatus {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [scriptblock]$Request = {
      param($Uri)
      Invoke-WebRequest -Uri $Uri -Method Get -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    }
  )

  try {
    $builder = [UriBuilder]::new($BaseUrl)
    if ($builder.Host -ceq 'host.docker.internal') {
      $builder.Host = '127.0.0.1'
    }
    $builder.Path = $builder.Path.TrimEnd('/') + '/v0/health/liveness'
    $response = & $Request $builder.Uri.AbsoluteUri
    $statusCode = [int]$response.StatusCode
    if ($statusCode -ge 200 -and $statusCode -lt 300) {
      return 'reachable'
    }
  } catch {
    return 'unavailable'
  }
  return 'unavailable'
}

function Assert-HostedLocalComposePolicy {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$RenderedJson,
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [scriptblock]$PolicyCheck
  )

  if ($null -ne $PolicyCheck) {
    if ((& $PolicyCheck $RenderedJson $StateRoot) -ne $true) {
      throw 'Rendered hosted-local Compose failed the hosted-local policy check.'
    }
    return
  }

  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $python) {
    throw 'Python is required to run the hosted-local Compose policy check.'
  }
  $repository = Get-HostedLocalRepositoryRoot
  $checker = Join-Path $repository 'scripts\check_compose.py'
  Assert-RegularReadableFile -Path $checker -Label 'Compose policy checker'
  $transactionId = [Guid]::NewGuid().ToString('N')
  $temporary = Join-Path $StateRoot "config\.compose-policy-$transactionId.json"
  $temporaryCode = Join-Path $StateRoot "config\.compose-policy-$transactionId.py"
  $code = @'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
from check_compose import validate_hosted_local

validate_hosted_local(
    json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig")),
    Path(sys.argv[3]),
)
'@
  try {
    Write-AtomicUtf8File -Path $temporary -Value $RenderedJson
    Write-AtomicUtf8File -Path $temporaryCode -Value $code
    & $python.Source $temporaryCode $checker $temporary $StateRoot
    if ($LASTEXITCODE -ne 0) {
      throw 'Rendered hosted-local Compose failed the hosted-local policy check.'
    }
  } finally {
    foreach ($path in @($temporary, $temporaryCode)) {
      if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
      }
    }
  }
}

function Get-HostedLocalApiHealth {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$StateRoot
  )

  $containerIds = @(Invoke-HostedLocalCompose `
      -RepositoryRoot $RepositoryRoot `
      -StateRoot $StateRoot `
      -Arguments @('ps', '--quiet', 'api'))
  $containerIds = @($containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
  if ($containerIds.Count -ne 1) {
    return 'missing'
  }
  $health = @(& docker inspect --format '{{.State.Health.Status}}' ([string]$containerIds[0]).Trim())
  if ($LASTEXITCODE -ne 0 -or $health.Count -ne 1) {
    return 'unknown'
  }
  return ([string]$health[0]).Trim()
}

function Assert-HostedLocalProjectLabels {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Resources)

  foreach ($resource in $Resources) {
    if (-not [string]::Equals(
        [string]$resource.Project,
        'parserium-hosted-local',
        [StringComparison]::Ordinal
      )) {
      throw "Hosted-local $($resource.Kind) $($resource.Name) has project label '$($resource.Project)'."
    }
  }
}

function Get-HostedLocalProjectResources {
  [CmdletBinding()]
  param()

  $resources = @()
  $containerIds = @(& docker ps --all --filter 'name=parserium-hosted-local' --format '{{.ID}}')
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not discover hosted-local containers.'
  }
  foreach ($containerId in $containerIds) {
    if ([string]::IsNullOrWhiteSpace([string]$containerId)) {
      continue
    }
    $detailsJson = @(& docker inspect `
        --format '{{json .}}' `
        ([string]$containerId).Trim())
    if ($LASTEXITCODE -ne 0 -or $detailsJson.Count -ne 1) {
      throw 'Could not inspect a discovered hosted-local container.'
    }
    try {
      $details = ([string]$detailsJson[0]) | ConvertFrom-Json -ErrorAction Stop
    } catch {
      throw 'Could not parse a discovered hosted-local container.'
    }
    $resources += [PSCustomObject]@{
      Kind = 'container'
      Name = [string]$details.Name
      Project = [string]$details.Config.Labels.'com.docker.compose.project'
    }
  }

  $volumeNames = @(& docker volume ls --filter 'name=parserium-hosted-local' --quiet)
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not discover hosted-local volumes.'
  }
  foreach ($volumeName in $volumeNames) {
    if ([string]::IsNullOrWhiteSpace([string]$volumeName)) {
      continue
    }
    $detailsJson = @(& docker volume inspect `
        --format '{{json .}}' `
        ([string]$volumeName).Trim())
    if ($LASTEXITCODE -ne 0 -or $detailsJson.Count -ne 1) {
      throw 'Could not inspect a discovered hosted-local volume.'
    }
    try {
      $details = ([string]$detailsJson[0]) | ConvertFrom-Json -ErrorAction Stop
    } catch {
      throw 'Could not parse a discovered hosted-local volume.'
    }
    $resources += [PSCustomObject]@{
      Kind = 'volume'
      Name = [string]$details.Name
      Project = [string]$details.Labels.'com.docker.compose.project'
    }
  }
  return @($resources)
}
