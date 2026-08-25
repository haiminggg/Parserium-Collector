$ErrorActionPreference = 'Stop'
$project = 'parserium-foundation-verification'
$composeFile = 'deploy/development/compose.yaml'
$runningOnWindows = $env:OS -eq 'Windows_NT'
if ($runningOnWindows) {
  & './scripts/bootstrap-local.ps1'
} else {
  & sh './scripts/bootstrap-local.sh'
}
$python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
New-Item -ItemType Directory -Path '.local' -Force | Out-Null
$imageLock = Get-Content -LiteralPath 'deploy/images.lock.json' -Raw | ConvertFrom-Json
foreach ($entry in $imageLock.images.PSObject.Properties.Value) {
  docker buildx imagetools inspect "$($entry.reference)@$($entry.digest)" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Image digest check failed for $($entry.reference)." }
}

try {
  docker build --target backend-test --tag parserium-collector-backend-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Backend gate failed.' }
  docker build --target web-test --tag parserium-collector-web-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Browser unit gate failed.' }
  docker build --target browser-test --tag parserium-collector-browser-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Browser acceptance image build failed.' }
  docker build --target runtime --tag parserium-collector:dev .
  if ($LASTEXITCODE -ne 0) { throw 'Runtime image build failed.' }

  $mount = '{0}:/workspace' -f (Get-Location).Path
  docker run --rm --volume $mount --workdir /workspace/backend --entrypoint /app/.venv/bin/python parserium-collector-backend-test:verify scripts/export_openapi.py --check --output ../contracts/openapi/dashboard-v1.json
  if ($LASTEXITCODE -ne 0) { throw 'OpenAPI contract is stale.' }

  $typeHash = (Get-FileHash 'apps/web/src/generated/dashboard-v1.d.ts' -Algorithm SHA256).Hash
  $contractOutput = docker run --rm parserium-collector-web-test:verify sh -lc 'sha256sum src/generated/dashboard-v1.d.ts > /tmp/expected && npm run contract:generate >/dev/null && sha256sum --check /tmp/expected >/dev/null && sha256sum src/generated/dashboard-v1.d.ts'
  if ($LASTEXITCODE -ne 0) { throw 'Browser contract generation failed.' }
  $containerHash = (($contractOutput | Select-Object -Last 1) -split '\s+')[0].ToUpperInvariant()
  if ($typeHash -ne $containerHash) {
    throw 'Browser contract is stale.'
  }

  docker compose -p $project -f $composeFile config --format json --output '.local/compose.json'
  if ($LASTEXITCODE -ne 0) { throw 'Rendered Compose generation failed.' }
  & $python scripts/check_compose.py .local/compose.json

  docker compose -p $project -f $composeFile up --detach --build
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
    docker compose -p $project -f $composeFile logs --no-color
    throw 'Stack readiness failed.'
  }

  $edgeNetwork = "${project}_edge"
  foreach ($browserProject in @('desktop-chromium', 'mobile-chromium')) {
    $recoveryOutput = @(
      docker compose -p $project -f $composeFile run --rm --no-deps api `
        python -m parserium_collector.cli.session_recovery
    )
    if ($LASTEXITCODE -ne 0) { throw 'Pairing-code recovery failed.' }
    $codeLines = @($recoveryOutput | Where-Object { $_ -like 'LOCAL PAIRING CODE: *' })
    if ($codeLines.Count -ne 1) { throw 'Recovery did not return exactly one pairing code.' }
    $pairingCode = ($codeLines[0] -split ': ', 2)[1].Trim()
    if ($pairingCode -notmatch '^[A-Za-z0-9_-]{24}$') {
      throw 'Recovery returned an invalid pairing-code format.'
    }
    docker run --rm --ipc=host --network $edgeNetwork `
      --env PLAYWRIGHT_BASE_URL=http://api:8080 `
      --env PLAYWRIGHT_PAIRING_CODE=$pairingCode `
      parserium-collector-browser-test:verify `
      npm run test:e2e -- --project=$browserProject
    $pairingCode = $null
    $recoveryOutput = $null
    if ($LASTEXITCODE -ne 0) { throw "Browser gate failed for $browserProject." }
  }
  Write-Output 'PASS: complete platform foundation verification.'
} finally {
  if ($project -ne 'parserium-foundation-verification') {
    throw 'Refusing cleanup for an unexpected Compose project.'
  }
  docker compose -p $project -f $composeFile down --volumes --remove-orphans
}
