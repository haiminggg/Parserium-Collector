$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$configure = Join-Path $repository 'scripts\hosted-local\configure-google.ps1'
if (-not (Test-Path -LiteralPath $configure -PathType Leaf)) {
  throw "Hosted-local Google configuration script is missing: $configure"
}

$suffix = [Guid]::NewGuid().ToString('N')
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stateRoot = [IO.Path]::GetFullPath(
  (Join-Path $temporaryRoot "parserium-hosted-local-google-state-$suffix")
)
$fixtureRoot = [IO.Path]::GetFullPath(
  (Join-Path $temporaryRoot "parserium-hosted-local-google-fixtures-$suffix")
)
$callback = 'https://localhost:8443/api/v1/auth/callback'
$clientId = 'client-id.apps.googleusercontent.com'
$secret = 'google-client-secret-value-that-is-long-enough'

function Write-JsonFixture {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][object]$Value
  )

  $path = Join-Path $fixtureRoot $Name
  [IO.File]::WriteAllText(
    $path,
    ($Value | ConvertTo-Json -Depth 5),
    [Text.UTF8Encoding]::new($false)
  )
  return $path
}

function Assert-RejectedFixture {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][object]$Value,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$SensitiveValue
  )

  $path = Write-JsonFixture -Name $Name -Value $Value
  $captured = ''
  $failure = $null
  try {
    $captured = (& $configure -ClientJson $path -StateRoot $stateRoot *>&1 | Out-String)
  } catch {
    $failure = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($failure)) {
    throw "Invalid Google fixture was accepted: $Name"
  }
  $combined = $captured + $failure
  if (
    -not [string]::IsNullOrEmpty($SensitiveValue) -and
    $combined.IndexOf($SensitiveValue, [StringComparison]::Ordinal) -ge 0
  ) {
    throw "Rejected Google fixture disclosed its secret: $Name"
  }
}

try {
  New-Item -ItemType Directory -Path (Join-Path $stateRoot 'config') -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $stateRoot 'secrets') -Force | Out-Null
  New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
  $environmentPath = Join-Path $stateRoot 'config\hosted-local.env'
  [IO.File]::WriteAllText(
    $environmentPath,
    "DASHBOARD_DEPLOYMENT_MODE=hosted`nDASHBOARD_OIDC_PROVIDER_LABEL=Google`n",
    [Text.UTF8Encoding]::new($false)
  )

  $valid = @{
    web = @{
      client_id = $clientId
      client_secret = $secret
      redirect_uris = @($callback)
    }
  }
  $validPath = Write-JsonFixture -Name 'valid.json' -Value $valid
  $firstOutput = (& $configure -ClientJson $validPath -StateRoot $stateRoot *>&1 | Out-String)
  $secondOutput = (& $configure -ClientJson $validPath -StateRoot $stateRoot *>&1 | Out-String)
  if (
    $firstOutput.IndexOf($secret, [StringComparison]::Ordinal) -ge 0 -or
    $secondOutput.IndexOf($secret, [StringComparison]::Ordinal) -ge 0
  ) {
    throw 'Google configuration output disclosed the client secret.'
  }
  if ($firstOutput.IndexOf($clientId, [StringComparison]::Ordinal) -lt 0) {
    throw 'Google configuration output did not identify the imported client ID.'
  }
  if ($firstOutput.IndexOf($callback, [StringComparison]::Ordinal) -lt 0) {
    throw 'Google configuration output did not identify the validated callback.'
  }

  $clientIdLines = @(
    Get-Content -LiteralPath $environmentPath |
      Where-Object { $_ -match '^DASHBOARD_OIDC_CLIENT_ID=' }
  )
  if ($clientIdLines.Count -ne 1) {
    throw 'Hosted-local environment did not contain exactly one Google client ID.'
  }
  if ($clientIdLines[0] -ne "DASHBOARD_OIDC_CLIENT_ID=$clientId") {
    throw 'Hosted-local environment contained the wrong Google client ID.'
  }
  $secretPath = Join-Path $stateRoot 'secrets\google_client_secret'
  if ([IO.File]::ReadAllText($secretPath) -cne $secret) {
    throw 'Google client secret file did not contain only the exact secret.'
  }
  $beforeEnvironment = Get-FileHash -LiteralPath $environmentPath -Algorithm SHA256
  $beforeSecret = Get-FileHash -LiteralPath $secretPath -Algorithm SHA256

  Assert-RejectedFixture -Name 'installed.json' -SensitiveValue 'installed-secret-value' -Value @{
    installed = @{
      client_id = 'installed.apps.googleusercontent.com'
      client_secret = 'installed-secret-value'
      redirect_uris = @($callback)
    }
  }
  Assert-RejectedFixture -Name 'missing-secret.json' -SensitiveValue '' -Value @{
    web = @{
      client_id = $clientId
      redirect_uris = @($callback)
    }
  }
  Assert-RejectedFixture -Name 'empty-client.json' -SensitiveValue $secret -Value @{
    web = @{
      client_id = ''
      client_secret = $secret
      redirect_uris = @($callback)
    }
  }
  Assert-RejectedFixture -Name 'wrong-callback.json' -SensitiveValue $secret -Value @{
    web = @{
      client_id = $clientId
      client_secret = $secret
      redirect_uris = @('https://localhost:8443/wrong-callback')
    }
  }

  $afterEnvironment = Get-FileHash -LiteralPath $environmentPath -Algorithm SHA256
  $afterSecret = Get-FileHash -LiteralPath $secretPath -Algorithm SHA256
  if ($beforeEnvironment.Hash -ne $afterEnvironment.Hash) {
    throw 'Rejected Google fixtures changed the hosted-local environment.'
  }
  if ($beforeSecret.Hash -ne $afterSecret.Hash) {
    throw 'Rejected Google fixtures changed the hosted-local secret.'
  }
  $leftovers = @(
    Get-ChildItem -LiteralPath (Join-Path $stateRoot 'config') -Force |
      Where-Object { $_.Name -match '\.(staged|backup|discard)$' }
    Get-ChildItem -LiteralPath (Join-Path $stateRoot 'secrets') -Force |
      Where-Object { $_.Name -match '\.(staged|backup|discard)$' }
  )
  if ($leftovers.Count -ne 0) {
    throw 'Google configuration left staged or backup files behind.'
  }

  Write-Output 'PASS: hosted-local Google Web client import is validated and non-leaking.'
} finally {
  $tempPrefix = $temporaryRoot.TrimEnd('\') + '\'
  foreach ($path in @($stateRoot, $fixtureRoot)) {
    $resolved = [IO.Path]::GetFullPath($path)
    if (-not $resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove an unexpected Google test path: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
      Remove-Item -LiteralPath $resolved -Recurse -Force
    }
  }
}
