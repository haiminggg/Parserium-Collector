$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$composeFile = Join-Path $repository 'deploy\development\compose.yaml'
$configFile = Join-Path $repository '.local\config.env'
& (Join-Path $repository 'scripts\bootstrap-local.ps1') | Out-Null

$renderedText = docker compose `
  --env-file $configFile `
  -f $composeFile `
  config `
  --format json
if ($LASTEXITCODE -ne 0) {
  throw 'Rendered Compose generation failed.'
}
$rendered = $renderedText | ConvertFrom-Json

$apiNetworks = @($rendered.services.api.networks.PSObject.Properties.Name)
$workerNetworks = @($rendered.services.worker.networks.PSObject.Properties.Name)
foreach ($entry in @(
  @{ Name = 'api'; Networks = $apiNetworks },
  @{ Name = 'worker'; Networks = $workerNetworks }
)) {
  if (
    $entry.Networks.Count -ne 2 -or
    $entry.Networks -notcontains 'private' -or
    $entry.Networks -notcontains 'edge'
  ) {
    throw "$($entry.Name) must join exactly the private and edge networks."
  }
}
foreach ($name in @('db', 'migrate', 'storage-init')) {
  $networks = @($rendered.services.$name.networks.PSObject.Properties.Name)
  if ($networks.Count -ne 1 -or $networks[0] -ne 'private') {
    throw "$name must join only the private network."
  }
}

$expectedSourceLine = @(
  Get-Content -LiteralPath $configFile |
    Where-Object { $_ -match '^PARSERIUM_EXPORT_ROOT=' }
)
if ($expectedSourceLine.Count -ne 1) {
  throw 'Local configuration must contain exactly one export root.'
}
$expectedSource = ($expectedSourceLine[0] -split '=', 2)[1].Replace('\', '/').TrimEnd('/')
$binds = @()
foreach ($name in $rendered.services.PSObject.Properties.Name) {
  foreach ($volume in @($rendered.services.$name.volumes)) {
    if ($null -ne $volume -and $volume.type -eq 'bind') {
      $binds += [PSCustomObject]@{ Service = $name; Volume = $volume }
    }
  }
}
if ($binds.Count -ne 2) {
  throw 'Exactly API and worker must receive one host bind mount each.'
}
foreach ($bind in $binds) {
  $source = ([string]$bind.Volume.source).Replace('\', '/').TrimEnd('/')
  if (-not $source.Equals($expectedSource, [StringComparison]::OrdinalIgnoreCase)) {
    throw "$($bind.Service) has an unexpected host bind source: $source"
  }
  if ($bind.Volume.target -ne '/exports') {
    throw "$($bind.Service) bind mount must target /exports."
  }
}
$apiBind = @($binds | Where-Object { $_.Service -eq 'api' })
$workerBind = @($binds | Where-Object { $_.Service -eq 'worker' })
if ($apiBind.Count -ne 1 -or $apiBind[0].Volume.read_only -ne $true) {
  throw 'API must receive one read-only export bind mount.'
}
if ($workerBind.Count -ne 1 -or $workerBind[0].Volume.read_only -eq $true) {
  throw 'Worker must receive one writable export bind mount.'
}

$requiredSettings = @(
  'DASHBOARD_EXPORT_ROOT',
  'DASHBOARD_DOWNLOAD_MAX_BYTES',
  'DASHBOARD_DOWNLOAD_CONNECT_TIMEOUT_SECONDS',
  'DASHBOARD_DOWNLOAD_READ_TIMEOUT_SECONDS',
  'DASHBOARD_DOWNLOAD_TOTAL_TIMEOUT_SECONDS',
  'DASHBOARD_DOWNLOAD_MAX_REDIRECTS',
  'DASHBOARD_DOWNLOAD_MAX_ATTEMPTS',
  'DASHBOARD_DOWNLOAD_RETRY_BASE_SECONDS',
  'DASHBOARD_DOWNLOAD_LEASE_SECONDS',
  'DASHBOARD_DOWNLOAD_ALLOWED_PUBLIC_PORTS',
  'DASHBOARD_DOWNLOAD_PRIVATE_ALLOWLIST',
  'DASHBOARD_DOCX_MAX_EXPANDED_BYTES',
  'DASHBOARD_DOCX_MAX_EXPANSION_RATIO'
)
foreach ($name in @('api', 'worker')) {
  $environment = $rendered.services.$name.environment
  foreach ($setting in $requiredSettings) {
    if ($null -eq $environment.PSObject.Properties[$setting]) {
      throw "$name is missing acquisition setting $setting."
    }
  }
  if ($environment.DASHBOARD_EXPORT_ROOT -ne '/exports') {
    throw "$name must use the container export root /exports."
  }
}

Write-Output 'PASS: rendered Compose acquisition mounts, settings, and egress are contained.'
