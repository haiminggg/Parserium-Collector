$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$localRoot = Join-Path $repository '.local'
$testRoot = Join-Path $localRoot ("compose-policy-test-{0}" -f [Guid]::NewGuid().ToString('N'))
$composeFile = Join-Path $repository 'deploy\development\compose.yaml'
$verificationComposeFile = Join-Path $repository 'deploy\verification\compose.yaml'
$configFile = Join-Path $localRoot 'config.env'
$checker = Join-Path $repository 'scripts\check_compose.py'
$python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

function Assert-CheckerFailure {
  param(
    [Parameter(Mandatory = $true)][object]$Document,
    [Parameter(Mandatory = $true)][string]$LocalConfig,
    [Parameter(Mandatory = $true)][string]$Expected,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $casePath = Join-Path $testRoot "$Name.json"
  [IO.File]::WriteAllText(
    $casePath,
    ($Document | ConvertTo-Json -Depth 100),
    [Text.UTF8Encoding]::new($false)
  )
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = & $python $checker $casePath --local-config $LocalConfig 2>&1
    $checkerExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($checkerExitCode -eq 0) {
    throw "$Name unexpectedly passed the Compose policy checker."
  }
  if (($output -join [Environment]::NewLine) -notmatch [Regex]::Escape($Expected)) {
    throw "$Name returned an unexpected policy result: $output"
  }
}

function Assert-VerificationCheckerFailure {
  param(
    [Parameter(Mandatory = $true)][object]$Document,
    [Parameter(Mandatory = $true)][string]$LocalConfig,
    [Parameter(Mandatory = $true)][string]$BaseConfig,
    [Parameter(Mandatory = $true)][string]$Expected,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $casePath = Join-Path $testRoot "$Name.json"
  [IO.File]::WriteAllText(
    $casePath,
    ($Document | ConvertTo-Json -Depth 100),
    [Text.UTF8Encoding]::new($false)
  )
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = & $python $checker $BaseConfig `
      --local-config $LocalConfig `
      --verification-config $casePath 2>&1
    $checkerExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($checkerExitCode -eq 0) {
    throw "$Name unexpectedly passed the verification Compose policy checker."
  }
  if (($output -join [Environment]::NewLine) -notmatch [Regex]::Escape($Expected)) {
    throw "$Name returned an unexpected verification policy result: $output"
  }
}

try {
  New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
  & (Join-Path $repository 'scripts\bootstrap-local.ps1') | Out-Null
  $baseJson = docker compose `
    --env-file $configFile `
    -f $composeFile `
    config `
    --format json
  if ($LASTEXITCODE -ne 0) { throw 'Rendered Compose generation failed.' }
  $basePath = Join-Path $testRoot 'base.json'
  [IO.File]::WriteAllText($basePath, $baseJson, [Text.UTF8Encoding]::new($false))

  & $python $checker $basePath --local-config $configFile
  if ($LASTEXITCODE -ne 0) {
    throw 'The unmodified development Compose file failed policy validation.'
  }

  $verificationJson = docker compose `
    --profile hosted `
    --env-file $configFile `
    -f $composeFile `
    -f $verificationComposeFile `
    config `
    --format json
  if ($LASTEXITCODE -ne 0) { throw 'Rendered verification Compose generation failed.' }

  $missingWorkerEgress = $baseJson | ConvertFrom-Json
  $missingWorkerEgress.services.worker.networks.PSObject.Properties.Remove('edge')
  Assert-CheckerFailure `
    -Document $missingWorkerEgress `
    -LocalConfig $configFile `
    -Expected 'worker must join exactly the edge and private networks' `
    -Name 'missing-worker-egress'

  $unrelatedEgress = $baseJson | ConvertFrom-Json
  $unrelatedEgress.services.migrate.networks | Add-Member -NotePropertyName edge -NotePropertyValue $null
  Assert-CheckerFailure `
    -Document $unrelatedEgress `
    -LocalConfig $configFile `
    -Expected 'migrate must join only the private network' `
    -Name 'unrelated-egress'

  $broadConfig = Join-Path $testRoot 'broad.env'
  [IO.File]::WriteAllText(
    $broadConfig,
    "PARSERIUM_EXPORT_ROOT=C:/`n",
    [Text.UTF8Encoding]::new($false)
  )
  Assert-CheckerFailure `
    -Document ($baseJson | ConvertFrom-Json) `
    -LocalConfig $broadConfig `
    -Expected 'export bind source must not be a filesystem root' `
    -Name 'broad-export-bind'

  $missingSetting = $baseJson | ConvertFrom-Json
  $missingSetting.services.worker.environment.PSObject.Properties.Remove(
    'DASHBOARD_DOWNLOAD_MAX_BYTES'
  )
  Assert-CheckerFailure `
    -Document $missingSetting `
    -LocalConfig $configFile `
    -Expected 'worker is missing acquisition settings' `
    -Name 'missing-acquisition-setting'

  $missingDeploymentMode = $baseJson | ConvertFrom-Json
  $missingDeploymentMode.services.api.environment.PSObject.Properties.Remove(
    'DASHBOARD_DEPLOYMENT_MODE'
  )
  Assert-CheckerFailure `
    -Document $missingDeploymentMode `
    -LocalConfig $configFile `
    -Expected 'api must explicitly use self_hosted deployment mode' `
    -Name 'missing-deployment-mode'

  $staleMigration = $baseJson | ConvertFrom-Json
  $staleMigration.services.worker.environment.DASHBOARD_EXPECTED_MIGRATION = (
    '0006_cloud_artifact_storage'
  )
  Assert-CheckerFailure `
    -Document $staleMigration `
    -LocalConfig $configFile `
    -Expected 'worker must expect migration 0009_unified_activity_history' `
    -Name 'stale-migration'

  $missingScratch = $baseJson | ConvertFrom-Json
  $missingScratch.services.api.environment.PSObject.Properties.Remove(
    'DASHBOARD_SCRATCH_ROOT'
  )
  Assert-CheckerFailure `
    -Document $missingScratch `
    -LocalConfig $configFile `
    -Expected 'api must use the dedicated scratch storage root' `
    -Name 'missing-scratch-root'

  $publishedMinio = $verificationJson | ConvertFrom-Json
  $publishedMinio.services.minio | Add-Member -NotePropertyName ports -NotePropertyValue @(
    [PSCustomObject]@{
      mode = 'ingress'
      target = 9000
      published = '19000'
      protocol = 'tcp'
    }
  )
  Assert-VerificationCheckerFailure `
    -Document $publishedMinio `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'minio must not publish a host port' `
    -Name 'published-minio'

  $minioEgress = $verificationJson | ConvertFrom-Json
  $minioEgress.services.minio.networks | Add-Member -NotePropertyName edge -NotePropertyValue $null
  Assert-VerificationCheckerFailure `
    -Document $minioEgress `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'minio must join only the private network' `
    -Name 'minio-egress'

  $hostedFilesystem = $verificationJson | ConvertFrom-Json
  $hostedFilesystem.services.'hosted-api' | Add-Member `
    -NotePropertyName volumes `
    -NotePropertyValue @(
      [PSCustomObject]@{
        type = 'volume'
        source = 'storage'
        target = '/var/lib/parserium-collector'
      }
    )
  Assert-VerificationCheckerFailure `
    -Document $hostedFilesystem `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'hosted-api must not mount filesystem durable storage' `
    -Name 'hosted-filesystem-storage'

  $insecureStorage = $verificationJson | ConvertFrom-Json
  $insecureStorage.services.'hosted-worker'.environment.DASHBOARD_STORAGE_ENDPOINT = (
    'http://minio:9000'
  )
  Assert-VerificationCheckerFailure `
    -Document $insecureStorage `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'hosted-worker must configure DASHBOARD_STORAGE_ENDPOINT' `
    -Name 'insecure-hosted-storage'

  $sharedSigningEndpoint = $verificationJson | ConvertFrom-Json
  $sharedSigningEndpoint.services.'hosted-api'.environment | Add-Member `
    -NotePropertyName DASHBOARD_STORAGE_SIGNED_URL_ENDPOINT `
    -NotePropertyValue 'https://localhost:9000'
  Assert-VerificationCheckerFailure `
    -Document $sharedSigningEndpoint `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'shared verification environment must leave the signed URL endpoint unset' `
    -Name 'shared-verification-signing-endpoint'

  $wrongProviderLabel = $verificationJson | ConvertFrom-Json
  $wrongProviderLabel.services.'hosted-api'.environment.DASHBOARD_OIDC_PROVIDER_LABEL = (
    'Identity provider'
  )
  Assert-VerificationCheckerFailure `
    -Document $wrongProviderLabel `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'hosted-api must render the Google provider label' `
    -Name 'wrong-hosted-provider-label'

  $missingHostedWrappingKey = $verificationJson | ConvertFrom-Json
  $missingHostedWrappingKey.secrets.PSObject.Properties.Remove(
    'firecrawl_credential_wrapping_key'
  )
  Assert-VerificationCheckerFailure `
    -Document $missingHostedWrappingKey `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'credential wrapping key secret is missing' `
    -Name 'missing-hosted-wrapping-key'

  $hostedGlobalFirecrawl = $verificationJson | ConvertFrom-Json
  $hostedGlobalFirecrawl.services.'hosted-api'.environment | Add-Member `
    -NotePropertyName DASHBOARD_FIRECRAWL_BASE_URL `
    -NotePropertyValue 'https://api.firecrawl.dev'
  Assert-VerificationCheckerFailure `
    -Document $hostedGlobalFirecrawl `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'must not configure a global Firecrawl endpoint' `
    -Name 'hosted-global-firecrawl'

  foreach ($allowlistCase in @(
      @{ Name = 'hosted-cidr-private-allowlist'; Value = '["10.0.0.0/8"]' },
      @{ Name = 'hosted-wildcard-private-allowlist'; Value = '["*.internal.example"]' }
    )) {
    $hostedUnsafeAllowlist = $verificationJson | ConvertFrom-Json
    $hostedUnsafeAllowlist.services.'hosted-api'.environment | Add-Member `
      -NotePropertyName DASHBOARD_FIRECRAWL_REMOTE_PRIVATE_ALLOWLIST `
      -NotePropertyValue $allowlistCase.Value `
      -Force
    Assert-VerificationCheckerFailure `
      -Document $hostedUnsafeAllowlist `
      -LocalConfig $configFile `
      -BaseConfig $basePath `
      -Expected 'private allowlist must contain exact hostnames only' `
      -Name $allowlistCase.Name
  }

  $hostedFirecrawlWithoutBearer = $verificationJson | ConvertFrom-Json
  $hostedFirecrawlWithoutBearer.services.'test-firecrawl'.secrets = @(
    $hostedFirecrawlWithoutBearer.services.'test-firecrawl'.secrets |
      Where-Object { $_.source -ne 'firecrawl_test_bearer' }
  )
  Assert-VerificationCheckerFailure `
    -Document $hostedFirecrawlWithoutBearer `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'must mount only generated bearer and TLS material' `
    -Name 'hosted-firecrawl-without-bearer'

  $hostedFirecrawlLegacyMode = $verificationJson | ConvertFrom-Json
  $hostedFirecrawlLegacyMode.services.'test-firecrawl'.command += '--legacy-http'
  Assert-VerificationCheckerFailure `
    -Document $hostedFirecrawlLegacyMode `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'must use the authenticated TLS fixture mode' `
    -Name 'hosted-firecrawl-legacy-mode'

  $hostedUnrelatedWrappingKey = $verificationJson | ConvertFrom-Json
  $hostedUnrelatedWrappingKey.services.db.secrets += [PSCustomObject]@{
    source = 'firecrawl_credential_wrapping_key'
    target = 'firecrawl_credential_wrapping_key'
  }
  Assert-VerificationCheckerFailure `
    -Document $hostedUnrelatedWrappingKey `
    -LocalConfig $configFile `
    -BaseConfig $basePath `
    -Expected 'credential wrapping key must be mounted only by API and worker' `
    -Name 'hosted-db-wrapping-key'

  Write-Output 'PASS: Compose policy rejects unsafe local and hosted verification variants.'
} finally {
  $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
  $resolvedLocalRoot = [IO.Path]::GetFullPath($localRoot)
  if (
    $resolvedTestRoot.StartsWith(
      $resolvedLocalRoot.TrimEnd('\') + '\compose-policy-test-',
      [StringComparison]::OrdinalIgnoreCase
    ) -and
    (Test-Path -LiteralPath $resolvedTestRoot)
  ) {
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
  }
}
