$ErrorActionPreference = 'Stop'

$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$common = Join-Path $repository 'scripts\hosted-local\common.ps1'
if (-not (Test-Path -LiteralPath $common -PathType Leaf)) {
  throw "Hosted-local common library is missing: $common"
}
. $common

$reportedRepository = Get-HostedLocalRepositoryRoot
if (-not $reportedRepository.Equals($repository, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Unexpected hosted-local repository root: $reportedRepository"
}

$default = Resolve-HostedLocalStateRoot -RepositoryRoot $repository
$expected = [IO.Path]::GetFullPath("${repository}.local\hosted-local")
if (-not $default.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Unexpected hosted-local default state root: $default"
}

foreach ($unsafe in @($repository, (Split-Path -Qualifier $repository), $HOME)) {
  $message = $null
  try {
    Assert-SafeHostedLocalStateRoot -StateRoot $unsafe -RepositoryRoot $repository
  } catch {
    $message = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($message)) {
    throw "Unsafe root was accepted: $unsafe"
  }
}

$testRoot = [IO.Path]::GetFullPath(
  (Join-Path ([IO.Path]::GetTempPath()) ('parserium-hosted-local-common-' + [Guid]::NewGuid().ToString('N')))
)
$previousStateRoot = $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT
$previousConfigFile = $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE

try {
  New-Item -ItemType Directory -Path $testRoot | Out-Null
  Assert-SafeHostedLocalStateRoot -StateRoot $testRoot -RepositoryRoot $repository

  $collision = Join-Path $testRoot 'file-collision'
  [IO.File]::WriteAllText($collision, 'collision', [Text.UTF8Encoding]::new($false))
  $collisionMessage = $null
  try {
    Assert-SafeHostedLocalStateRoot -StateRoot $collision -RepositoryRoot $repository
  } catch {
    $collisionMessage = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($collisionMessage)) {
    throw 'An existing non-directory state root was accepted.'
  }

  $secret = New-RandomBase64UrlSecret -Length 64
  $secondSecret = New-RandomBase64UrlSecret -Length 64
  if ($secret -notmatch '^[A-Za-z0-9_-]{64}$') {
    throw 'Generated secret was not 64-character base64url text.'
  }
  if ($secret -eq $secondSecret) {
    throw 'Two generated secrets unexpectedly matched.'
  }

  $atomicPath = Join-Path $testRoot 'atomic.txt'
  $capturedOutput = (& { Write-AtomicUtf8File -Path $atomicPath -Value $secret } *>&1 | Out-String)
  if ($capturedOutput.IndexOf($secret, [StringComparison]::Ordinal) -ge 0) {
    throw 'Atomic write output disclosed the secret value.'
  }
  Assert-RegularReadableFile -Path $atomicPath -Label 'Atomic test file'
  if ([IO.File]::ReadAllText($atomicPath) -ne $secret) {
    throw 'Atomic write did not preserve the exact value.'
  }
  Write-AtomicUtf8File -Path $atomicPath -Value $secondSecret
  if ([IO.File]::ReadAllText($atomicPath) -ne $secondSecret) {
    throw 'Atomic overwrite did not replace the exact value.'
  }
  $bytes = [IO.File]::ReadAllBytes($atomicPath)
  if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    throw 'Atomic write unexpectedly added a UTF-8 byte-order mark.'
  }
  if (Test-Path -LiteralPath (Join-Path $testRoot '.atomic.txt.*.tmp')) {
    throw 'Atomic write left a temporary sibling.'
  }

  $pkiDirectory = Join-Path $testRoot 'pki'
  New-Item -ItemType Directory -Path $pkiDirectory | Out-Null
  $pkiNames = @(
    'ca-key.pem',
    'ca.pem',
    'minio-cert.pem',
    'minio-key.pem',
    'parserium-cert.pem',
    'parserium-key.pem'
  )
  foreach ($name in $pkiNames) {
    [IO.File]::WriteAllText(
      (Join-Path $pkiDirectory $name),
      "test-$name",
      [Text.UTF8Encoding]::new($false)
    )
  }
  $script:pkiOwnershipDockerArguments = $null
  function global:docker {
    $script:pkiOwnershipDockerArguments = @($args | ForEach-Object { [string]$_ })
    $global:LASTEXITCODE = 0
  }
  try {
    Set-HostedLocalPkiContainerOwner `
      -StateRoot $testRoot `
      -PkiDirectoryName 'pki' `
      -Image 'parserium-test-image'
  } finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
  }
  $expectedPkiOwnershipArguments = @(
    'run', '--rm',
    '--network', 'none',
    '--read-only',
    '--cap-drop', 'ALL',
    '--cap-add', 'CHOWN',
    '--cap-add', 'DAC_OVERRIDE',
    '--security-opt', 'no-new-privileges',
    '--user', '0:0',
    '--volume', ($testRoot.Replace('\', '/') + ':/host-state'),
    '--entrypoint', 'chown',
    'parserium-test-image',
    '0:0'
  ) + @($pkiNames | ForEach-Object { "/host-state/pki/$_" })
  if (
    ($script:pkiOwnershipDockerArguments -join "`n") -cne
    ($expectedPkiOwnershipArguments -join "`n")
  ) {
    throw 'Hosted-local PKI ownership normalization used unexpected Docker arguments.'
  }

  $directoryMessage = $null
  try {
    Assert-RegularReadableFile -Path $testRoot -Label 'Directory fixture'
  } catch {
    $directoryMessage = $_.Exception.Message
  }
  if ([string]::IsNullOrWhiteSpace($directoryMessage)) {
    throw 'A directory was accepted as a regular readable file.'
  }

  $composeArguments = @(Get-HostedLocalComposeArguments `
    -RepositoryRoot $repository `
    -StateRoot $testRoot)
  $expectedArguments = @(
    '--project-name', 'parserium-hosted-local',
    '--env-file', (Join-Path $testRoot 'config\hosted-local.env'),
    '-f', (Join-Path $repository 'deploy\hosted-local\compose.yaml')
  )
  if (($composeArguments -join "`n") -ne ($expectedArguments -join "`n")) {
    throw 'Hosted-local Compose arguments were not exact.'
  }

  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = 'previous-state-root'
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = 'previous-config-file'
  $script:dockerInvocation = $null
  function global:docker {
    $script:dockerInvocation = @{
      Arguments = @($args)
      StateRoot = $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT
      ConfigFile = $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE
    }
    $global:LASTEXITCODE = 0
  }
  try {
    Invoke-HostedLocalCompose `
      -RepositoryRoot $repository `
      -StateRoot $testRoot `
      -Arguments @('config', '--quiet')
  } finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
  }
  if ($script:dockerInvocation.Arguments[0] -ne 'compose') {
    throw 'Hosted-local Compose invocation did not call docker compose.'
  }
  if ($script:dockerInvocation.StateRoot -ne $testRoot.Replace('\', '/')) {
    throw 'Hosted-local Compose invocation exposed the wrong state root.'
  }
  if ($env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT -ne 'previous-state-root') {
    throw 'Hosted-local Compose invocation did not restore the state-root environment variable.'
  }
  if ($env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE -ne 'previous-config-file') {
    throw 'Hosted-local Compose invocation did not restore the config-file environment variable.'
  }

  $target = Join-Path $testRoot 'junction-target'
  $junction = Join-Path $testRoot 'junction'
  New-Item -ItemType Directory -Path $target | Out-Null
  $junctionCreated = $false
  try {
    New-Item -ItemType Junction -Path $junction -Target $target -ErrorAction Stop | Out-Null
    $junctionCreated = $true
  } catch {
    Write-Output 'INFO: reparse-point fixture creation is unavailable; that assertion was skipped.'
  }
  if ($junctionCreated) {
    $reparseMessage = $null
    try {
      Assert-SafeHostedLocalStateRoot `
        -StateRoot (Join-Path $junction 'state') `
        -RepositoryRoot $repository
    } catch {
      $reparseMessage = $_.Exception.Message
    }
    if ([string]::IsNullOrWhiteSpace($reparseMessage)) {
      throw 'A state root beneath a reparse point was accepted.'
    }
    Remove-Item -LiteralPath $junction -Force
  }

  Write-Output 'PASS: hosted-local common helpers enforce bounded paths and non-leaking writes.'
} finally {
  $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $previousStateRoot
  $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = $previousConfigFile
  if (Test-Path -LiteralPath $testRoot) {
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTestRoot.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove an unexpected test path: $resolvedTestRoot"
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
  }
}
