$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$commandDirectory = Join-Path $repository 'scripts\hosted-local'
$lifecycleLibrary = Join-Path $commandDirectory 'lifecycle.ps1'
$requiredCommands = @('start.ps1', 'stop.ps1', 'status.ps1', 'logs.ps1', 'invite.ps1', 'reset.ps1')

if (-not (Test-Path -LiteralPath $lifecycleLibrary -PathType Leaf)) {
  throw "Hosted-local lifecycle library is missing: $lifecycleLibrary"
}
foreach ($commandName in $requiredCommands) {
  $commandPath = Join-Path $commandDirectory $commandName
  if (-not (Test-Path -LiteralPath $commandPath -PathType Leaf)) {
    throw "Hosted-local command is missing: $commandPath"
  }
}

. (Join-Path $commandDirectory 'common.ps1')
. (Join-Path $commandDirectory 'pki.ps1')
. $lifecycleLibrary

function Assert-Rejected {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Operation,
    [Parameter(Mandatory = $true)][string]$Expected,
    [Parameter(Mandatory = $true)][string]$Label
  )

  $failure = $null
  try {
    & $Operation
  } catch {
    $failure = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($failure)) {
    throw "Unsafe hosted-local command case was accepted: $Label"
  }
  if ($failure -notmatch [regex]::Escape($Expected)) {
    throw "Wrong failure for ${Label}: $failure"
  }
}

function New-TestIdentity {
  param(
    [string]$Subject = 'CN=Parserium hosted-local development CA',
    [string]$SerialNumber = 'A123',
    [string]$Sha256 = ('A' * 64),
    [DateTime]$NotBeforeUtc = [DateTime]::UtcNow.AddMinutes(-5),
    [DateTime]$NotAfterUtc = [DateTime]::UtcNow.AddDays(30),
    [bool]$IsCertificateAuthority = $true,
    [object]$Certificate = $null
  )

  return [PSCustomObject]@{
    Subject = $Subject
    SerialNumber = $SerialNumber
    Sha256 = $Sha256
    NotBeforeUtc = $NotBeforeUtc
    NotAfterUtc = $NotAfterUtc
    IsCertificateAuthority = $IsCertificateAuthority
    Certificate = $Certificate
  }
}

$suffix = [Guid]::NewGuid().ToString('N')
$testRoot = [IO.Path]::GetFullPath(
  (Join-Path ([IO.Path]::GetTempPath()) "parserium-hosted-local-commands-$suffix")
)
$global:hostedLocalDockerCalls = [Collections.Generic.List[string]]::new()
$global:hostedLocalDockerMode = 'success'

function global:docker {
  $arguments = @($args | ForEach-Object { [string]$_ })
  $joined = $arguments -join ' '
  $global:hostedLocalDockerCalls.Add($joined)
  $global:LASTEXITCODE = 0

  if ($joined -match 'compose version$') {
    'Docker Compose version test-double'
    return
  }
  if ($joined -match 'ps --status running --services api$') {
    if ($global:hostedLocalDockerMode -ne 'api-unhealthy') {
      'api'
    }
    return
  }
  if ($joined -match 'ps --quiet api$') {
    'test-api-container'
    return
  }
  if ($joined -ceq 'ps --filter publish=8443 --format {{.ID}}') {
    'test-port-container'
    return
  }
  if ($joined -ceq 'inspect --format {{json .Config.Labels}} test-port-container') {
    '{"com.docker.compose.project":"parserium-hosted-local","com.docker.compose.service":"api"}'
    return
  }
  if (
    $global:hostedLocalDockerMode -ceq 'resource-inspection' -and
    $joined -ceq 'ps --all --filter name=parserium-hosted-local --format {{.ID}}'
  ) {
    'test-resource-container'
    return
  }
  if (
    $global:hostedLocalDockerMode -ceq 'resource-inspection' -and
    $joined -ceq 'inspect --format {{json .}} test-resource-container'
  ) {
    '{"Name":"/parserium-hosted-local-api-1","Config":{"Labels":{"com.docker.compose.project":"parserium-hosted-local"}}}'
    return
  }
  if (
    $global:hostedLocalDockerMode -ceq 'resource-inspection' -and
    $joined -ceq 'volume ls --filter name=parserium-hosted-local --quiet'
  ) {
    'parserium-hosted-local-database'
    return
  }
  if (
    $global:hostedLocalDockerMode -ceq 'resource-inspection' -and
    $joined -ceq 'volume inspect --format {{json .}} parserium-hosted-local-database'
  ) {
    '{"Name":"parserium-hosted-local-database","Labels":{"com.docker.compose.project":"parserium-hosted-local"}}'
    return
  }
  if ($joined -match 'index \.Config\.Labels') {
    $global:LASTEXITCODE = 1
    return
  }
  if ($joined -match '^inspect .*test-api-container$') {
    'healthy'
    return
  }
  if ($joined -match 'run --rm --no-deps -T api python -m parserium_collector\.cli\.invite') {
    'https://localhost:8443/#invite=test-double-token'
    return
  }
  if ($joined -match '^ps .*--format') {
    return
  }
  if ($joined -match '^volume ls ') {
    return
  }
}

try {
  New-Item -ItemType Directory -Path (Join-Path $testRoot 'config') -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $testRoot 'secrets') -Force | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $testRoot 'pki') -Force | Out-Null

  $configurationPath = Join-Path $testRoot 'config\hosted-local.env'
  [IO.File]::WriteAllText(
    $configurationPath,
    (@(
        'DASHBOARD_PUBLIC_ORIGIN=https://localhost:8443',
        'DASHBOARD_OIDC_CLIENT_ID=test-client.apps.googleusercontent.com',
        'DASHBOARD_FIRECRAWL_BASE_URL=http://host.docker.internal:3002'
      ) -join "`n") + "`n",
    [Text.UTF8Encoding]::new($false)
  )
  $secretValues = @{
    db_password = 'D' * 64
    session_signing_secret = 'S' * 64
    google_client_secret = 'google-test-secret'
    minio_access_key = 'parserium-' + ('A' * 32)
    minio_secret_key = 'M' * 64
  }
  foreach ($entry in $secretValues.GetEnumerator()) {
    [IO.File]::WriteAllText(
      (Join-Path $testRoot "secrets\$($entry.Key)"),
      $entry.Value,
      [Text.UTF8Encoding]::new($false)
    )
  }

  foreach ($name in @(
      'ca-key.pem',
      'ca.pem',
      'minio-cert.pem',
      'minio-key.pem',
      'parserium-cert.pem',
      'parserium-key.pem'
    )) {
    [IO.File]::WriteAllText(
      (Join-Path $testRoot "pki\$name"),
      "test-$name-content",
      [Text.UTF8Encoding]::new($false)
    )
  }

  $setupCommand = Join-Path $commandDirectory 'setup.ps1'
  & $setupCommand -StateRoot $testRoot | Out-Null
  $wrappingKeyPath = Join-Path $testRoot 'secrets\firecrawl_credential_wrapping_key'
  $firstWrappingKey = [IO.File]::ReadAllText($wrappingKeyPath)
  try {
    $decodedWrappingKey = [Convert]::FromBase64String($firstWrappingKey)
  } catch {
    throw 'Hosted-local setup did not create a valid base64 Firecrawl wrapping key.'
  }
  if (
    $decodedWrappingKey.Length -ne 32 -or
    [Convert]::ToBase64String($decodedWrappingKey) -cne $firstWrappingKey
  ) {
    throw 'Hosted-local setup did not create a canonical 256-bit Firecrawl wrapping key.'
  }
  & $setupCommand -StateRoot $testRoot | Out-Null
  if ([IO.File]::ReadAllText($wrappingKeyPath) -cne $firstWrappingKey) {
    throw 'Hosted-local setup replaced the preserved Firecrawl wrapping key.'
  }
  if (
    [IO.File]::ReadAllText($configurationPath) -match
    '(?m)^DASHBOARD_FIRECRAWL_BASE_URL='
  ) {
    throw 'Hosted-local setup preserved the obsolete global Firecrawl endpoint.'
  }

  Assert-HostedLocalSecretFiles -StateRoot $testRoot
  $environment = Read-HostedLocalEnvironment -Path $configurationPath
  Assert-HostedLocalGoogleConfiguration -Environment $environment

  $missingGoogle = @{}
  foreach ($key in $environment.Keys) {
    if ($key -ne 'DASHBOARD_OIDC_CLIENT_ID') {
      $missingGoogle[$key] = @($environment[$key])
    }
  }
  Assert-Rejected `
    -Operation { Assert-HostedLocalGoogleConfiguration -Environment $missingGoogle } `
    -Expected 'exactly once' `
    -Label 'missing Google client configuration'

  $duplicateGoogle = @{}
  foreach ($key in $environment.Keys) {
    $duplicateGoogle[$key] = @($environment[$key])
  }
  $duplicateGoogle['DASHBOARD_OIDC_CLIENT_ID'] = @('first', 'second')
  Assert-Rejected `
    -Operation { Assert-HostedLocalGoogleConfiguration -Environment $duplicateGoogle } `
    -Expected 'exactly once' `
    -Label 'duplicate Google client configuration'

  $sessionPath = Join-Path $testRoot 'secrets\session_signing_secret'
  $originalSession = [IO.File]::ReadAllText($sessionPath)
  [IO.File]::WriteAllText($sessionPath, 'short', [Text.UTF8Encoding]::new($false))
  Assert-Rejected `
    -Operation { Assert-HostedLocalSecretFiles -StateRoot $testRoot } `
    -Expected 'Session signing' `
    -Label 'short session secret'
  [IO.File]::WriteAllText($sessionPath, $originalSession, [Text.UTF8Encoding]::new($false))

  [IO.File]::WriteAllText(
    $wrappingKeyPath,
    [Convert]::ToBase64String((New-Object byte[] 31)),
    [Text.UTF8Encoding]::new($false)
  )
  Assert-Rejected `
    -Operation { Assert-HostedLocalSecretFiles -StateRoot $testRoot } `
    -Expected 'wrapping key' `
    -Label 'wrong-length Firecrawl wrapping key'
  [IO.File]::WriteAllText(
    $wrappingKeyPath,
    $firstWrappingKey,
    [Text.UTF8Encoding]::new($false)
  )

  $statusOutput = @(& (Join-Path $commandDirectory 'status.ps1') -StateRoot $testRoot)
  if (($statusOutput -join "`n") -notmatch 'Firecrawl connections: workspace-managed') {
    throw 'status.ps1 did not report workspace-managed Firecrawl connections.'
  }

  $expiredCa = New-TestIdentity -NotAfterUtc ([DateTime]::UtcNow.AddSeconds(-1))
  Assert-Rejected `
    -Operation { Assert-HostedLocalCertificateWindow -Identity $expiredCa -Label 'CA' -RequireCa } `
    -Expected 'expired' `
    -Label 'expired certificate authority'

  $caIdentity = New-TestIdentity
  $trustRecord = [PSCustomObject]@{
    store = 'CurrentUser/Root'
    subject = $caIdentity.Subject
    serial_number = $caIdentity.SerialNumber
    sha256 = $caIdentity.Sha256
  }
  $certificateStoreTestDouble = @()
  Assert-Rejected `
    -Operation {
      Assert-HostedLocalCaTrust `
        -Identity $caIdentity `
        -TrustRecord $trustRecord `
        -Certificates $certificateStoreTestDouble
    } `
    -Expected 'not installed' `
    -Label 'untrusted certificate authority'

  Assert-HostedLocalLoopbackAddresses -Addresses @('127.0.0.1', '::1')
  Assert-Rejected `
    -Operation { Assert-HostedLocalLoopbackAddresses -Addresses @('127.0.0.1', '192.0.2.4') } `
    -Expected 'non-loopback' `
    -Label 'unsafe localhost resolution'

  $portOwnerRecords = @(Get-HostedLocalPortOwnerRecords -Port 8443)
  if (
    $portOwnerRecords.Count -ne 1 -or
    $portOwnerRecords[0].Project -cne 'parserium-hosted-local' -or
    $portOwnerRecords[0].Service -cne 'api'
  ) {
    throw 'Hosted-local port ownership did not parse exact Compose labels from JSON.'
  }

  $global:hostedLocalDockerMode = 'resource-inspection'
  try {
    $projectResources = @(Get-HostedLocalProjectResources)
  } finally {
    $global:hostedLocalDockerMode = 'success'
  }
  if (
    $projectResources.Count -ne 2 -or
    @($projectResources | Where-Object { $_.Project -cne 'parserium-hosted-local' }).Count -ne 0 -or
    @($projectResources | Where-Object { $_.Kind -ceq 'container' }).Count -ne 1 -or
    @($projectResources | Where-Object { $_.Kind -ceq 'volume' }).Count -ne 1
  ) {
    throw 'Hosted-local project resources did not parse exact Compose labels from JSON.'
  }

  $freePortAvailable = $true
  $freePortOwners = if ($freePortAvailable) {
    @()
  } else {
    @([PSCustomObject]@{ Project = 'parserium-hosted-local'; Service = 'api' })
  }
  Assert-HostedLocalPortOwnership `
    -Port 8443 `
    -Available $freePortAvailable `
    -OwnerRecords $freePortOwners `
    -ExpectedService 'api'

  Assert-HostedLocalPortOwnership `
    -Port 8443 `
    -Available $false `
    -OwnerRecords @([PSCustomObject]@{ Project = 'parserium-hosted-local'; Service = 'api' }) `
    -ExpectedService 'api'
  Assert-Rejected `
    -Operation {
      Assert-HostedLocalPortOwnership `
        -Port 8443 `
        -Available $false `
        -OwnerRecords @([PSCustomObject]@{ Project = 'another-project'; Service = 'api' }) `
        -ExpectedService 'api'
    } `
    -Expected 'occupied' `
    -Label 'occupied API port'

  $unsafePolicyCalls = 0
  Assert-Rejected `
    -Operation {
      Assert-HostedLocalComposePolicy `
        -RenderedJson '{"name":"unsafe"}' `
        -StateRoot $testRoot `
        -PolicyCheck {
          param($RenderedJson, $StateRoot)
          $script:unsafePolicyCalls++
          return $false
        }
    } `
    -Expected 'policy' `
    -Label 'unsafe rendered Compose'
  if ($unsafePolicyCalls -ne 1) {
    throw 'Hosted-local Compose policy test double was not called exactly once.'
  }

  $dockerCallsBeforeFirecrawl = $global:hostedLocalDockerCalls.Count
  $firecrawlAttempts = 0
  $firecrawlRequestTestDouble = {
    param($Uri)
    $script:firecrawlAttempts++
    if ($script:firecrawlAttempts -eq 1) {
      throw [Net.WebException]::new('test transport failure')
    }
    return [PSCustomObject]@{ StatusCode = 200 }
  }
  $firstFirecrawl = Get-HostedLocalFirecrawlStatus `
    -BaseUrl 'http://host.docker.internal:3002' `
    -Request $firecrawlRequestTestDouble
  $secondFirecrawl = Get-HostedLocalFirecrawlStatus `
    -BaseUrl 'http://host.docker.internal:3002' `
    -Request $firecrawlRequestTestDouble
  if ($firstFirecrawl -ne 'unavailable' -or $secondFirecrawl -ne 'reachable') {
    throw 'Firecrawl liveness did not recover from unavailable to reachable.'
  }
  if ($global:hostedLocalDockerCalls.Count -ne $dockerCallsBeforeFirecrawl) {
    throw 'Firecrawl reachability checks unexpectedly restarted Compose.'
  }

  $logsCommand = Join-Path $commandDirectory 'logs.ps1'
  & $logsCommand -Service api -Tail 125 -StateRoot $testRoot | Out-Null
  $expectedComposePrefix = @(
    'compose',
    '--project-name', 'parserium-hosted-local',
    '--env-file', (Join-Path $testRoot 'config\hosted-local.env'),
    '-f', (Join-Path $repository 'deploy\hosted-local\compose.yaml')
  ) -join ' '
  if ($global:hostedLocalDockerCalls[-1] -cne "$expectedComposePrefix logs --no-color --tail 125 api") {
    throw "logs.ps1 used unexpected Docker arguments: $($global:hostedLocalDockerCalls[-1])"
  }
  Assert-Rejected `
    -Operation { & $logsCommand -Service unknown -Tail 125 -StateRoot $testRoot } `
    -Expected 'ValidateSet' `
    -Label 'unknown logs service'
  Assert-Rejected `
    -Operation { & $logsCommand -Service api -Tail 2001 -StateRoot $testRoot } `
    -Expected '2000' `
    -Label 'oversized logs tail'

  $callsBeforeReset = $global:hostedLocalDockerCalls.Count
  Assert-Rejected `
    -Operation {
      & (Join-Path $commandDirectory 'reset.ps1') `
        -ConfirmProject 'wrong-project' `
        -StateRoot $testRoot
    } `
    -Expected 'parserium-hosted-local' `
    -Label 'wrong reset confirmation'
  $newResetCalls = @($global:hostedLocalDockerCalls | Select-Object -Skip $callsBeforeReset)
  if (@($newResetCalls | Where-Object { $_ -match '\bdown\b' }).Count -ne 0) {
    throw 'Rejected reset executed a Compose down mutation.'
  }

  Assert-HostedLocalProjectLabels `
    -Resources @(
      [PSCustomObject]@{ Kind = 'container'; Name = 'api'; Project = 'parserium-hosted-local' },
      [PSCustomObject]@{ Kind = 'volume'; Name = 'database'; Project = 'parserium-hosted-local' }
    )
  Assert-Rejected `
    -Operation {
      Assert-HostedLocalProjectLabels `
        -Resources @(
          [PSCustomObject]@{ Kind = 'volume'; Name = 'database'; Project = 'other-project' }
        )
    } `
    -Expected 'other-project' `
    -Label 'foreign reset resource'

  $callsBeforeInvite = $global:hostedLocalDockerCalls.Count
  Assert-Rejected `
    -Operation {
      & (Join-Path $commandDirectory 'invite.ps1') `
        -Email 'not-an-email' `
        -WorkspaceName 'Test workspace' `
        -StateRoot $testRoot
    } `
    -Expected 'email' `
    -Label 'invalid invitation email'
  $newInviteCalls = @($global:hostedLocalDockerCalls | Select-Object -Skip $callsBeforeInvite)
  if (@($newInviteCalls | Where-Object { $_ -match '\b(?:exec|run)\b' }).Count -ne 0) {
    throw 'Rejected invitation executed the invitation CLI.'
  }

  $stopOutput = @(& (Join-Path $commandDirectory 'stop.ps1') -StateRoot $testRoot)
  if ($global:hostedLocalDockerCalls[-1] -cne "$expectedComposePrefix stop") {
    throw "stop.ps1 used unexpected Docker arguments: $($global:hostedLocalDockerCalls[-1])"
  }
  if (($stopOutput -join "`n") -notmatch 'volumes were preserved') {
    throw 'stop.ps1 did not confirm volume preservation.'
  }

  $inviteOutput = @(& (Join-Path $commandDirectory 'invite.ps1') `
      -Email 'owner@example.com' `
      -WorkspaceName 'Test workspace' `
      -ExpiresHours 48 `
      -StateRoot $testRoot)
  $expectedInviteArguments = (
    "$expectedComposePrefix run --rm --no-deps -T api python -m parserium_collector.cli.invite create " +
    '--email owner@example.com --workspace-name Test workspace --role owner --expires-hours 48'
  )
  if ($global:hostedLocalDockerCalls[-1] -cne $expectedInviteArguments) {
    throw "invite.ps1 used unexpected Docker arguments: $($global:hostedLocalDockerCalls[-1])"
  }
  if (
    $inviteOutput.Count -ne 1 -or
    $inviteOutput[0] -cne (
      'Sensitive, one-time invitation URL: ' +
      'https://localhost:8443/#invite=test-double-token'
    )
  ) {
    throw 'invite.ps1 did not print the one-time invitation URL exactly once as sensitive.'
  }

  & (Join-Path $commandDirectory 'reset.ps1') `
    -ConfirmProject 'parserium-hosted-local' `
    -StateRoot $testRoot | Out-Null
  if (
    $global:hostedLocalDockerCalls[-1] -cne (
      "$expectedComposePrefix down --volumes --remove-orphans"
    )
  ) {
    throw "reset.ps1 used unexpected Docker arguments: $($global:hostedLocalDockerCalls[-1])"
  }
  foreach ($preservedPath in @(
      $configurationPath,
      (Join-Path $testRoot 'secrets\db_password'),
      (Join-Path $testRoot 'pki')
    )) {
    if (-not (Test-Path -LiteralPath $preservedPath)) {
      throw "reset.ps1 did not preserve external hosted-local state: $preservedPath"
    }
  }

  Write-Output 'PASS: hosted-local lifecycle commands enforce preflight and exact-project safety.'
} finally {
  Remove-Item function:\docker -ErrorAction SilentlyContinue
  Remove-Variable hostedLocalDockerCalls -Scope Global -ErrorAction SilentlyContinue
  Remove-Variable hostedLocalDockerMode -Scope Global -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $testRoot) {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove an unexpected hosted-local command test path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
