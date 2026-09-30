$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRoot = [IO.Path]::GetFullPath((Join-Path $repository '.local'))
$testRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot ("bootstrap-config-test-{0}" -f [Guid]::NewGuid().ToString('N')))
)
if (-not $testRoot.StartsWith($localRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  throw "Test directory escapes the local root: $testRoot"
}

function Read-ConfigValue {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $matches = @(
    Get-Content -LiteralPath $Path |
      Where-Object { $_ -match ('^{0}=' -f [Regex]::Escape($Name)) }
  )
  if ($matches.Count -ne 1) {
    throw "Expected exactly one $Name entry in $Path."
  }
  return ($matches[0] -split '=', 2)[1]
}

try {
  $defaultLocal = Join-Path $testRoot 'default'
  & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
    -LocalDirectory $defaultLocal `
    -SecretDirectory (Join-Path $defaultLocal 'secrets') | Out-Null

  $defaultExport = [IO.Path]::GetFullPath((Join-Path $defaultLocal 'exports'))
  $defaultConfig = Join-Path $defaultLocal 'config.env'
  if (-not (Test-Path -LiteralPath $defaultExport -PathType Container)) {
    throw 'Bootstrap did not create the ignored default export directory.'
  }
  if (-not (Test-Path -LiteralPath $defaultConfig -PathType Leaf)) {
    throw 'Bootstrap did not create the local configuration file.'
  }
  $configuredDefault = (Read-ConfigValue -Path $defaultConfig -Name 'PARSERIUM_EXPORT_ROOT').Replace('/', '\')
  if (-not $configuredDefault.Equals($defaultExport, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected default export root: $configuredDefault"
  }
  foreach ($required in @(
    'DASHBOARD_DOWNLOAD_MAX_BYTES',
    'DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS',
    'DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS',
    'DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS',
    'DASHBOARD_DOWNLOAD_MAX_REDIRECTS',
    'DASHBOARD_DOWNLOAD_MAX_ATTEMPTS',
    'DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS',
    'DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST',
    'DASHBOARD_DOCX_MAX_EXPANDED_BYTES',
    'DASHBOARD_DOCX_MAX_EXPANSION_RATIO'
  )) {
    [void](Read-ConfigValue -Path $defaultConfig -Name $required)
  }

  $customLocal = Join-Path $testRoot 'custom'
  $customExport = Join-Path $testRoot 'chosen-export-root'
  New-Item -ItemType Directory -Path $customExport -Force | Out-Null
  & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
    -LocalDirectory $customLocal `
    -SecretDirectory (Join-Path $customLocal 'secrets') `
    -ExportDirectory $customExport | Out-Null
  $customConfig = Join-Path $customLocal 'config.env'
  $original = [IO.File]::ReadAllText($customConfig).Replace(
    'DASHBOARD_DOWNLOAD_MAX_BYTES=104857600',
    'DASHBOARD_DOWNLOAD_MAX_BYTES=209715200'
  )
  [IO.File]::WriteAllText($customConfig, $original, [Text.UTF8Encoding]::new($false))

  & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
    -LocalDirectory $customLocal `
    -SecretDirectory (Join-Path $customLocal 'secrets') | Out-Null
  if ([IO.File]::ReadAllText($customConfig) -ne $original) {
    throw 'Bootstrap replaced existing local configuration values.'
  }

  $invalidLocal = Join-Path $testRoot 'invalid'
  New-Item -ItemType Directory -Path $invalidLocal -Force | Out-Null
  [IO.File]::WriteAllText(
    (Join-Path $invalidLocal 'config.env'),
    "PARSERIUM_EXPORT_ROOT=relative\exports`n",
    [Text.UTF8Encoding]::new($false)
  )
  $failure = $null
  try {
    & (Join-Path $repository 'scripts\bootstrap-local.ps1') `
      -LocalDirectory $invalidLocal `
      -SecretDirectory (Join-Path $invalidLocal 'secrets') | Out-Null
  } catch {
    $failure = $_.Exception.Message
  }
  if ($failure -ne 'Configured export directory must be an absolute existing directory.') {
    throw "Unexpected invalid export result: $failure"
  }

  Write-Output 'PASS: bootstrap creates and preserves validated local acquisition configuration.'
} finally {
  if (Test-Path -LiteralPath $testRoot) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
  }
}
