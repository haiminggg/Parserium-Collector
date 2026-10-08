$ErrorActionPreference = 'Stop'
$project = 'parserium-foundation-test'
$composeFile = 'deploy\development\compose.yaml'
$configFile = '.local\config.env'
if (-not (Test-Path -LiteralPath $composeFile)) {
  throw 'EXPECTED RED: development Compose file is missing.'
}
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
  throw 'Port 8080 is already in use.'
}
& "$PSScriptRoot\..\..\scripts\bootstrap-local.ps1"
try {
  docker compose --env-file $configFile -p $project -f $composeFile up --detach --build
  if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }
  $deadline = (Get-Date).AddMinutes(3)
  do {
    Start-Sleep -Seconds 2
    try {
      $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/health/ready' -TimeoutSec 5
    } catch {
      $ready = $null
    }
  } until (($null -ne $ready -and $ready.overall -eq 'ready') -or (Get-Date) -gt $deadline)
  if ($null -eq $ready -or $ready.overall -ne 'ready') {
    docker compose --env-file $configFile -p $project -f $composeFile logs --no-color
    throw 'Foundation did not become ready within three minutes.'
  }
  $html = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/' -UseBasicParsing -TimeoutSec 10
  if ($html.Content -notmatch 'Parserium Collector') { throw 'Browser shell was not served.' }
  $config = docker compose --env-file $configFile -p $project -f $composeFile config --format json | ConvertFrom-Json
  $published = @(
    $config.services.PSObject.Properties.Value |
      Where-Object { $null -ne $_.ports } |
      ForEach-Object { $_.ports }
  )
  if ($published.Count -ne 1 -or $published[0].host_ip -ne '127.0.0.1' -or $published[0].published -ne '8080') {
    throw 'Exactly one loopback port must be published.'
  }
  foreach ($name in @('db', 'migrate', 'storage-init')) {
    $serviceNetworks = @($config.services.$name.networks.PSObject.Properties.Name)
    if ($serviceNetworks -contains 'edge') { throw "$name must remain private." }
  }
  $apiNetworks = @($config.services.api.networks.PSObject.Properties.Name)
  if ($apiNetworks.Count -ne 2 -or $apiNetworks -notcontains 'private' -or $apiNetworks -notcontains 'edge') {
    throw 'API must join the private and edge networks.'
  }
  $workerNetworks = @($config.services.worker.networks.PSObject.Properties.Name)
  if ($workerNetworks.Count -ne 2 -or $workerNetworks -notcontains 'private' -or $workerNetworks -notcontains 'edge') {
    throw 'Worker must join the private and edge networks.'
  }
  Write-Output 'PASS: foundation stack is ready and loopback-only.'
} finally {
  if ($project -ne 'parserium-foundation-test') { throw 'Refusing cleanup for an unexpected project.' }
  docker compose --env-file $configFile -p $project -f $composeFile down --volumes --remove-orphans
}
