$ErrorActionPreference = 'Stop'
$runId = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$project = "parserium-foundation-verification-$runId"
$composeFile = 'deploy/development/compose.yaml'
$verificationComposeFile = 'deploy/verification/compose.yaml'
$configFile = '.local/config.env'
$verificationPort = 18080
$localRoot = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path '.local'))
$verificationExportRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot 'foundation-verification-exports')
)
$verificationPkiRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot 'verification-pki')
)
$recoveryRoot = [IO.Path]::GetFullPath(
  (Join-Path $localRoot "storage-recovery-$runId")
)
$pathSeparators = [char[]]@(
  [IO.Path]::DirectorySeparatorChar,
  [IO.Path]::AltDirectorySeparatorChar
)
$localPrefix = $localRoot.TrimEnd($pathSeparators) + [IO.Path]::DirectorySeparatorChar
if (-not $verificationExportRoot.StartsWith($localPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Verification export directory escapes the ignored local root.'
}
if (-not $verificationPkiRoot.StartsWith($localPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Verification PKI directory escapes the ignored local root.'
}
if (-not $recoveryRoot.StartsWith($localPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Storage recovery directory escapes the ignored local root.'
}
$previousExportRoot = $env:PARSERIUM_EXPORT_ROOT
$previousVerificationPort = $env:PARSERIUM_VERIFICATION_PORT
$env:PARSERIUM_EXPORT_ROOT = $null
$env:PARSERIUM_VERIFICATION_PORT = $null
$databasePasswordPath = Join-Path (Get-Location).Path '.local/secrets/db_password'
$storageAccessKeyPath = Join-Path $verificationPkiRoot 'minio-access-key'
$storageSecretKeyPath = Join-Path $verificationPkiRoot 'minio-secret-key'
$storageCaBundlePath = Join-Path $verificationPkiRoot 'ca.pem'
$firecrawlBearerPath = Join-Path $verificationPkiRoot 'firecrawl-test-bearer'
$firecrawlWrappingKeyPath = Join-Path $verificationPkiRoot 'firecrawl-credential-wrapping-key'
$transcriptPath = Join-Path $recoveryRoot 'verification-output.log'
$verificationSucceeded = $false
$transcriptStarted = $false
$generatedSecrets = @()

function Assert-NoSensitiveMaterial {
  $needles = @(
    ('X-Amz-' + 'Signature'),
    ('PRIVATE ' + 'KEY-----')
  ) + @($generatedSecrets)
  $needles = @($needles | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  $trackedFiles = @(git ls-files)
  if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate tracked files for the sensitive-material scan.' }
  foreach ($path in $trackedFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
    $content = [IO.File]::ReadAllText([IO.Path]::GetFullPath($path))
    foreach ($needle in $needles) {
      if ($content.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        throw "Sensitive material was found in tracked file $path."
      }
    }
  }
  if (Test-Path -LiteralPath $recoveryRoot) {
    foreach ($file in Get-ChildItem -LiteralPath $recoveryRoot -File -Recurse) {
      $content = [IO.File]::ReadAllText($file.FullName)
      foreach ($needle in $needles) {
        if ($content.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
          throw "Sensitive material was found in captured verification output $($file.Name)."
        }
      }
    }
  }
}

function Invoke-S3AdapterTest {
  param(
    [Parameter(Mandatory = $true)][string]$Selector,
    [switch]$ExpectUnavailable
  )
  $arguments = @(
    'run', '--rm',
    '--network', "${project}_private",
    '--env', 'TEST_S3_ENDPOINT=https://minio:9000',
    '--env', 'TEST_S3_REGION=us-east-1',
    '--env', 'TEST_S3_BUCKET=parserium-verification-primary',
    '--env', 'TEST_S3_ACCESS_KEY_FILE=/run/secrets/storage_access_key',
    '--env', 'TEST_S3_SECRET_KEY_FILE=/run/secrets/storage_secret_key',
    '--env', 'TEST_S3_CA_BUNDLE_FILE=/run/secrets/storage_ca_bundle',
    '--volume', "${storageAccessKeyPath}:/run/secrets/storage_access_key:ro",
    '--volume', "${storageSecretKeyPath}:/run/secrets/storage_secret_key:ro",
    '--volume', "${storageCaBundlePath}:/run/secrets/storage_ca_bundle:ro"
  )
  if ($ExpectUnavailable) {
    $arguments += @('--env', 'TEST_S3_EXPECT_UNAVAILABLE=1')
  }
  $arguments += @(
    'parserium-collector-backend-test:verify',
    '.venv/bin/pytest', '-q', '-p', 'no:cacheprovider',
    $Selector, '--tb=no'
  )
  & docker @arguments
  if ($LASTEXITCODE -ne 0) { throw "S3 adapter verification failed for $Selector." }
}

function Invoke-FirecrawlRemoteContractTest {
  $arguments = @(
    'run', '--rm',
    '--network', "${project}_edge",
    '--env', 'SSL_CERT_FILE=/run/secrets/verification_ca_bundle',
    '--env', 'TEST_FIRECRAWL_ORIGIN=https://test-firecrawl:9444',
    '--env', 'TEST_FIRECRAWL_BEARER_FILE=/run/secrets/firecrawl_test_bearer',
    '--env', 'TEST_FIRECRAWL_CA_BUNDLE_FILE=/run/secrets/verification_ca_bundle',
    '--volume', "${firecrawlBearerPath}:/run/secrets/firecrawl_test_bearer:ro",
    '--volume', "${storageCaBundlePath}:/run/secrets/verification_ca_bundle:ro",
    'parserium-collector-backend-test:verify',
    '.venv/bin/pytest', '-q', '-p', 'no:cacheprovider',
    'tests/integration/test_firecrawl_remote_contract.py', '--tb=no'
  )
  & docker @arguments
  if ($LASTEXITCODE -ne 0) { throw 'Remote Firecrawl contract verification failed.' }
}

function Invoke-RecoveryPhase {
  param(
    [Parameter(Mandatory = $true)][string]$Phase,
    [Parameter(Mandatory = $true)][string]$ExerciseId,
    [Parameter(Mandatory = $true)][string]$DatabaseName
  )
  $arguments = @(
    'run', '--rm',
    '--network', "${project}_private",
    '--env', "TEST_RECOVERY_PHASE=$Phase",
    '--env', "TEST_RECOVERY_RUN_ID=$ExerciseId",
    '--env', 'TEST_DATABASE_HOST=db',
    '--env', "TEST_DATABASE_NAME=$DatabaseName",
    '--env', 'TEST_DATABASE_PASSWORD_FILE=/run/secrets/db_password',
    '--env', 'TEST_S3_ENDPOINT=https://minio:9000',
    '--env', 'TEST_S3_REGION=us-east-1',
    '--env', 'TEST_S3_BUCKET=parserium-verification-primary',
    '--env', 'TEST_S3_BACKUP_BUCKET=parserium-verification-backup',
    '--env', 'TEST_S3_ACCESS_KEY_FILE=/run/secrets/storage_access_key',
    '--env', 'TEST_S3_SECRET_KEY_FILE=/run/secrets/storage_secret_key',
    '--env', 'TEST_S3_CA_BUNDLE_FILE=/run/secrets/storage_ca_bundle',
    '--volume', "${databasePasswordPath}:/run/secrets/db_password:ro",
    '--volume', "${storageAccessKeyPath}:/run/secrets/storage_access_key:ro",
    '--volume', "${storageSecretKeyPath}:/run/secrets/storage_secret_key:ro",
    '--volume', "${storageCaBundlePath}:/run/secrets/storage_ca_bundle:ro",
    'parserium-collector-backend-test:verify',
    '.venv/bin/pytest', '-q', '-p', 'no:cacheprovider',
    'tests/integration/test_storage_recovery.py', '--tb=no'
  )
  & docker @arguments
  if ($LASTEXITCODE -ne 0) { throw "Storage recovery phase $Phase failed." }
}

function Invoke-RecoveryExercise {
  param([Parameter(Mandatory = $true)][string]$ExerciseId)
  if ($ExerciseId -notmatch '^[a-f0-9]{12,32}$') {
    throw 'Refusing a recovery exercise with an invalid identifier.'
  }
  $recoveryDatabase = "parserium_recovery_$ExerciseId"
  if ($recoveryDatabase -notmatch '^parserium_recovery_[a-f0-9]{12,32}$') {
    throw 'Refusing a recovery exercise with an invalid database name.'
  }
  $dumpName = "database-$ExerciseId.dump"
  $containerDumpPath = "/var/lib/postgresql/$dumpName"
  $hostDumpPath = Join-Path $recoveryRoot $dumpName
  $dbContainers = @(
    docker compose --env-file $configFile -p $project `
      -f $composeFile -f $verificationComposeFile ps -q db |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
  if ($LASTEXITCODE -ne 0 -or $dbContainers.Count -ne 1) {
    throw 'Recovery could not resolve exactly one database container.'
  }
  $dbContainer = $dbContainers[0].Trim()

  Invoke-RecoveryPhase `
    -Phase 'seed-and-backup' `
    -ExerciseId $ExerciseId `
    -DatabaseName 'parserium_collector'
  docker exec $dbContainer pg_dump `
    --username parserium_collector `
    --dbname parserium_collector `
    --format=custom `
    --file=$containerDumpPath
  if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL custom-format recovery dump failed.' }
  docker cp "${dbContainer}:$containerDumpPath" $hostDumpPath
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $hostDumpPath)) {
    throw 'Recovery dump copy to ignored local storage failed.'
  }

  Invoke-RecoveryPhase `
    -Phase 'empty-primary' `
    -ExerciseId $ExerciseId `
    -DatabaseName 'parserium_collector'

  $databaseCreated = $false
  $restoreSucceeded = $false
  try {
    docker exec $dbContainer createdb `
      --username parserium_collector `
      --owner parserium_collector `
      $recoveryDatabase
    if ($LASTEXITCODE -ne 0) { throw 'Clean recovery database creation failed.' }
    $databaseCreated = $true
    docker exec $dbContainer pg_restore `
      --exit-on-error `
      --no-owner `
      --username parserium_collector `
      --dbname $recoveryDatabase `
      $containerDumpPath
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL recovery restore failed.' }

    Invoke-RecoveryPhase `
      -Phase 'restore-and-verify' `
      -ExerciseId $ExerciseId `
      -DatabaseName $recoveryDatabase
    $restoreSucceeded = $true
  } finally {
    if ($databaseCreated) {
      docker exec $dbContainer dropdb `
        --username parserium_collector `
        $recoveryDatabase | Out-Null
      if ($LASTEXITCODE -ne 0) {
        if ($restoreSucceeded) { throw 'Recovery database cleanup failed.' }
        Write-Warning 'Recovery database cleanup also failed; preserving the original recovery error.'
      }
    }
  }
}

try {
  $runningOnWindows = $env:OS -eq 'Windows_NT'
  New-Item -ItemType Directory -Path '.local' -Force | Out-Null
  New-Item -ItemType Directory -Path $recoveryRoot -Force | Out-Null
  Start-Transcript -LiteralPath $transcriptPath | Out-Null
  $transcriptStarted = $true
  if ($runningOnWindows) {
    & './scripts/bootstrap-local.ps1'
    & './tests/integration/test_bootstrap_secrets.ps1'
    & './tests/integration/test_bootstrap_config.ps1'
    & './tests/integration/test_compose_acquisition_config.ps1'
    & './tests/integration/test_compose_policy.ps1'
    & './tests/integration/test_hosted_local_common.ps1'
    & './tests/integration/test_hosted_local_google_config.ps1'
    & './tests/integration/test_hosted_local_compose.ps1'
    & './tests/integration/test_hosted_local_commands.ps1'
  } else {
    & sh './scripts/bootstrap-local.sh'
    & sh './tests/integration/test_bootstrap_config.sh'
  }
  $python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
  $imageLock = Get-Content -LiteralPath 'deploy/images.lock.json' -Raw | ConvertFrom-Json
  foreach ($entry in $imageLock.images.PSObject.Properties.Value) {
    docker buildx imagetools inspect "$($entry.reference)@$($entry.digest)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Image digest check failed for $($entry.reference)." }
  }

  docker build --target backend-test --tag parserium-collector-backend-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Backend gate failed.' }
  if ($runningOnWindows) {
    & './tests/integration/test_hosted_local_pki.ps1' `
      -BackendTestImage 'parserium-collector-backend-test:verify'
  }
  docker build --target web-test --tag parserium-collector-web-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Browser unit gate failed.' }
  docker build --target browser-test --tag parserium-collector-browser-test:verify .
  if ($LASTEXITCODE -ne 0) { throw 'Browser acceptance image build failed.' }
  docker build --target runtime --tag parserium-collector:dev .
  if ($LASTEXITCODE -ne 0) { throw 'Runtime image build failed.' }
  docker build --target minio-verification --tag parserium-minio:verification .
  if ($LASTEXITCODE -ne 0) { throw 'Pinned MinIO verification image build failed.' }
  if ($runningOnWindows) {
    & './tests/integration/test_container_secret_handoff.ps1'
  }

  $mount = '{0}:/workspace' -f (Get-Location).Path
  if (Test-Path -LiteralPath $verificationPkiRoot) {
    Remove-Item -LiteralPath $verificationPkiRoot -Recurse -Force
  }
  New-Item -ItemType Directory -Path $verificationPkiRoot | Out-Null
  docker run --rm --volume $mount --workdir /workspace `
    --entrypoint /app/.venv/bin/python `
    parserium-collector-backend-test:verify `
    tests/fixtures/generate_verification_pki.py `
    /workspace/.local/verification-pki
  if ($LASTEXITCODE -ne 0) { throw 'Ephemeral verification PKI generation failed.' }
  $generatedSecrets = @(
    [IO.File]::ReadAllText($storageAccessKeyPath).Trim(),
    [IO.File]::ReadAllText($storageSecretKeyPath).Trim(),
    [IO.File]::ReadAllText($firecrawlBearerPath).Trim(),
    [IO.File]::ReadAllText($firecrawlWrappingKeyPath).Trim()
  )
  docker run --rm --volume $mount --workdir /workspace/backend --entrypoint /app/.venv/bin/python parserium-collector-backend-test:verify scripts/export_openapi.py --check --output ../contracts/openapi/dashboard-v1.json
  if ($LASTEXITCODE -ne 0) { throw 'OpenAPI contract is stale.' }

  $typeHash = (Get-FileHash 'apps/web/src/generated/dashboard-v1.d.ts' -Algorithm SHA256).Hash
  $contractOutput = docker run --rm parserium-collector-web-test:verify sh -lc 'sha256sum src/generated/dashboard-v1.d.ts > /tmp/expected && npm run contract:generate >/dev/null && sha256sum --check /tmp/expected >/dev/null && sha256sum src/generated/dashboard-v1.d.ts'
  if ($LASTEXITCODE -ne 0) { throw 'Browser contract generation failed.' }
  $containerHash = (($contractOutput | Select-Object -Last 1) -split '\s+')[0].ToUpperInvariant()
  if ($typeHash -ne $containerHash) {
    throw 'Browser contract is stale.'
  }

  docker compose --env-file $configFile -p $project -f $composeFile config --format json --output '.local/compose.json'
  if ($LASTEXITCODE -ne 0) { throw 'Rendered Compose generation failed.' }
  docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile config --format json --output '.local/verification-compose.json'
  if ($LASTEXITCODE -ne 0) { throw 'Rendered verification Compose generation failed.' }
  & $python scripts/check_compose.py .local/compose.json `
    --local-config $configFile `
    --verification-config .local/verification-compose.json

  New-Item -ItemType Directory -Path $verificationExportRoot -Force | Out-Null
  $env:PARSERIUM_EXPORT_ROOT = $verificationExportRoot.Replace('\', '/')
  $env:PARSERIUM_VERIFICATION_PORT = [string]$verificationPort
  if (
    $runningOnWindows -and
    (Get-NetTCPConnection -LocalPort $verificationPort -State Listen -ErrorAction SilentlyContinue)
  ) {
    throw "Verification port $verificationPort is already in use."
  }

  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile up --detach --no-build
  if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }
  $deadline = (Get-Date).AddMinutes(3)
  do {
    Start-Sleep -Seconds 2
    try {
      $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$verificationPort/api/v1/health/ready" -TimeoutSec 5
    } catch {
      $ready = $null
    }
  } until (($null -ne $ready -and $ready.overall -eq 'ready') -or (Get-Date) -gt $deadline)
  if ($null -eq $ready -or $ready.overall -ne 'ready') {
    docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile logs --no-color
    throw 'Stack readiness failed.'
  }

  Invoke-FirecrawlRemoteContractTest

  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile stop api worker
  if ($LASTEXITCODE -ne 0) { throw 'Could not isolate repository integration tests.' }
  docker run --rm --network "${project}_private" `
    --env DASHBOARD_DB_HOST=db `
    --env DASHBOARD_DB_PASSWORD_FILE=/run/secrets/db_password `
    --volume "${databasePasswordPath}:/run/secrets/db_password:ro" `
    parserium-collector-backend-test:verify `
    .venv/bin/pytest -q `
      tests/integration/test_tenancy_migration.py `
      tests/integration/test_analysis_migration.py `
      tests/integration/test_artifact_migration.py `
      tests/integration/test_firecrawl_connection_migration.py
  if ($LASTEXITCODE -ne 0) { throw 'Migration preservation gate failed.' }
  docker run --rm --network "${project}_private" `
    --env TEST_DATABASE_HOST=db `
    --env TEST_DATABASE_PASSWORD_FILE=/run/secrets/db_password `
    --volume "${databasePasswordPath}:/run/secrets/db_password:ro" `
    parserium-collector-backend-test:verify `
    .venv/bin/pytest -q `
      tests/integration/test_session_repository.py `
      tests/integration/test_acquisition_repository.py `
      tests/integration/test_analysis_repository.py `
      tests/integration/test_artifact_repository.py `
      tests/integration/test_artifact_workspace_isolation.py `
      tests/integration/test_acquisition_pipeline.py `
      tests/integration/test_firecrawl_connection_repository.py `
      tests/integration/test_firecrawl_connection_workspace_isolation.py `
      tests/integration/test_identity_repository.py `
      tests/integration/test_workspace_isolation.py
  if ($LASTEXITCODE -ne 0) { throw 'Repository and acquisition integration gate failed.' }
  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm migrate `
    alembic -c /app/alembic.ini stamp base --purge
  if ($LASTEXITCODE -ne 0) { throw 'Migration marker reset after integration tests failed.' }
  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm migrate
  if ($LASTEXITCODE -ne 0) { throw 'Schema restoration after integration tests failed.' }

  Invoke-S3AdapterTest -Selector 'tests/integration/test_s3_artifact_store.py'
  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile stop minio
  if ($LASTEXITCODE -ne 0) { throw 'Could not stop MinIO for interruption verification.' }
  try {
    Invoke-S3AdapterTest `
      -Selector 'tests/integration/test_s3_artifact_store.py::test_real_s3_reports_a_temporary_restart_as_unavailable' `
      -ExpectUnavailable
  } finally {
    docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile start minio
    if ($LASTEXITCODE -ne 0) { throw 'Could not restart MinIO after interruption verification.' }
    docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm --no-deps storage-bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Private bucket bootstrap failed after MinIO restart.' }
  }
  Invoke-S3AdapterTest `
    -Selector 'tests/integration/test_s3_artifact_store.py::test_real_s3_recovers_after_restart'

  Invoke-RecoveryExercise -ExerciseId "${runId}01"
  Invoke-RecoveryExercise -ExerciseId "${runId}02"

  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile start worker api
  if ($LASTEXITCODE -ne 0) { throw 'Could not restart the verification application.' }
  $deadline = (Get-Date).AddMinutes(3)
  do {
    Start-Sleep -Seconds 2
    try {
      $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$verificationPort/api/v1/health/ready" -TimeoutSec 5
    } catch {
      $ready = $null
    }
  } until (($null -ne $ready -and $ready.overall -eq 'ready') -or (Get-Date) -gt $deadline)
  if ($null -eq $ready -or $ready.overall -ne 'ready') {
    throw 'Verification application did not recover after repository tests.'
  }
  $edgeNetwork = "${project}_edge"
  foreach ($browserProject in @('desktop-chromium', 'mobile-chromium', 'visual-desktop')) {
    $recoveryOutput = @(
      docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm --no-deps api `
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

  docker compose --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile stop api worker
  if ($LASTEXITCODE -ne 0) { throw 'Could not stop the self-hosted application.' }
  docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile up --detach --no-build hosted-api hosted-worker
  if ($LASTEXITCODE -ne 0) { throw 'Could not start the hosted verification application.' }
  $hostedContainer = "${project}-hosted-api-1"
  $deadline = (Get-Date).AddMinutes(3)
  do {
    Start-Sleep -Seconds 2
    $hostedHealth = docker inspect --format '{{.State.Health.Status}}' $hostedContainer 2>$null
  } until (($hostedHealth -eq 'healthy') -or (Get-Date) -gt $deadline)
  if ($hostedHealth -ne 'healthy') {
    docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile logs --no-color hosted-api hosted-worker verification-oidc minio
    throw 'Hosted verification application did not become healthy.'
  }

  foreach ($hostedBrowserProject in @('hosted-chromium', 'hosted-mobile-chromium')) {
    $workspaceSuffix = if ($hostedBrowserProject -eq 'hosted-chromium') {
      'desktop'
    } else {
      'mobile'
    }
    $workspaceA = "Workspace A $workspaceSuffix"
    $workspaceB = "Workspace B $workspaceSuffix"
    $inviteAOutput = @(
      docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm --no-deps hosted-api `
        python -m parserium_collector.cli.invite create `
        --email owner-a@example.com `
        --workspace-name $workspaceA `
        --role owner `
        --expires-hours 1
    )
    if ($LASTEXITCODE -ne 0) { throw "Workspace A invitation creation failed for $hostedBrowserProject." }
    $inviteBOutput = @(
      docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile run --rm --no-deps hosted-api `
        python -m parserium_collector.cli.invite create `
        --email owner-b@example.com `
        --workspace-name $workspaceB `
        --role owner `
        --expires-hours 1
    )
    if ($LASTEXITCODE -ne 0) { throw "Workspace B invitation creation failed for $hostedBrowserProject." }
    $inviteALines = @($inviteAOutput | Where-Object { $_ -match '^https://hosted-api:8443/#invite=' })
    $inviteBLines = @($inviteBOutput | Where-Object { $_ -match '^https://hosted-api:8443/#invite=' })
    if ($inviteALines.Count -ne 1 -or $inviteBLines.Count -ne 1) {
      throw 'Hosted invitation commands returned an unexpected result.'
    }
    $inviteA = $inviteALines[0].Trim()
    $inviteB = $inviteBLines[0].Trim()
    docker run --rm --ipc=host --network $edgeNetwork `
      --env PLAYWRIGHT_BASE_URL=https://hosted-api:8443 `
      --env PLAYWRIGHT_INVITE_A=$inviteA `
      --env PLAYWRIGHT_INVITE_B=$inviteB `
      --env PLAYWRIGHT_WORKSPACE_A=$workspaceA `
      --env PLAYWRIGHT_WORKSPACE_B=$workspaceB `
      --env PLAYWRIGHT_FIRECRAWL_TOKEN_FILE=/run/secrets/firecrawl_test_bearer `
      --volume "${firecrawlBearerPath}:/run/secrets/firecrawl_test_bearer:ro" `
      parserium-collector-browser-test:verify `
      npm run test:e2e -- --project=$hostedBrowserProject
    $inviteA = $null
    $inviteB = $null
    $inviteAOutput = $null
    $inviteBOutput = $null
    if ($LASTEXITCODE -ne 0) { throw "Hosted browser gate failed for $hostedBrowserProject." }
  }
  Stop-Transcript | Out-Null
  $transcriptStarted = $false
  Assert-NoSensitiveMaterial
  $verificationSucceeded = $true
  Write-Output 'PASS: complete platform foundation verification.'
} catch {
  $verificationFailure = $_
  if ($transcriptStarted) {
    Stop-Transcript | Out-Null
    $transcriptStarted = $false
  }
  if (Test-Path -LiteralPath $configFile) {
    $diagnosticPath = Join-Path $recoveryRoot 'compose.log'
    docker compose --profile hosted --env-file $configFile -p $project `
      -f $composeFile -f $verificationComposeFile logs --no-color *>&1 |
      Set-Content -LiteralPath $diagnosticPath
  }
  try {
    Assert-NoSensitiveMaterial
  } catch {
    $verificationFailure = $_
  }
  throw $verificationFailure
} finally {
  if ($transcriptStarted) {
    Stop-Transcript | Out-Null
  }
  if ($project -notmatch '^parserium-foundation-verification-[a-f0-9]{12}$') {
    throw 'Refusing cleanup for an unexpected Compose project.'
  }
  docker compose --profile hosted --env-file $configFile -p $project -f $composeFile -f $verificationComposeFile down --volumes --remove-orphans
  $env:PARSERIUM_EXPORT_ROOT = $previousExportRoot
  $env:PARSERIUM_VERIFICATION_PORT = $previousVerificationPort
  if (Test-Path -LiteralPath $verificationExportRoot) {
    if ($runningOnWindows) {
      Add-Type -AssemblyName Microsoft.VisualBasic
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
        $verificationExportRoot,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
      )
    } else {
      Remove-Item -LiteralPath $verificationExportRoot -Recurse -Force
    }
  }
  if (Test-Path -LiteralPath $verificationPkiRoot) {
    Remove-Item -LiteralPath $verificationPkiRoot -Recurse -Force
  }
  if ($verificationSucceeded -and (Test-Path -LiteralPath $recoveryRoot)) {
    Remove-Item -LiteralPath $recoveryRoot -Recurse -Force
  }
}
