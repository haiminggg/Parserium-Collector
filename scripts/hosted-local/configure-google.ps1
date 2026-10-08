param(
  [Parameter(Mandatory = $true)][string]$ClientJson,
  [string]$StateRoot
)

$ErrorActionPreference = 'Stop'
$common = Join-Path $PSScriptRoot 'common.ps1'
if (-not (Test-Path -LiteralPath $common -PathType Leaf)) {
  throw "Hosted-local common library is missing: $common"
}
. $common

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot `
  -StateRoot $resolvedStateRoot `
  -RepositoryRoot $repository
if (-not (Test-Path -LiteralPath $resolvedStateRoot -PathType Container)) {
  throw 'Hosted-local state does not exist. Run setup.ps1 first.'
}

$configurationDirectory = Join-Path $resolvedStateRoot 'config'
$secretDirectory = Join-Path $resolvedStateRoot 'secrets'
foreach ($directory in @($configurationDirectory, $secretDirectory)) {
  if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
    throw "Hosted-local setup directory is missing: $directory"
  }
  Assert-NoReparsePointInPath -Path $directory
}

$resolvedClientJson = [IO.Path]::GetFullPath($ClientJson)
Assert-RegularReadableFile -Path $resolvedClientJson -Label 'Google client JSON'
try {
  $document = [IO.File]::ReadAllText($resolvedClientJson) | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw 'Google client JSON could not be parsed.'
}
$topLevelProperties = @($document.PSObject.Properties.Name)
if ($topLevelProperties.Count -ne 1 -or $topLevelProperties[0] -ne 'web') {
  throw 'Google client JSON must contain exactly one top-level web client.'
}
$web = $document.web
if ($null -eq $web -or $web -isnot [PSCustomObject]) {
  throw 'Google client JSON web client is invalid.'
}
$clientId = $web.client_id
$clientSecret = $web.client_secret
if (
  $clientId -isnot [string] -or
  [string]::IsNullOrWhiteSpace($clientId) -or
  $clientId -ne $clientId.Trim() -or
  $clientId -match '[\r\n\0]'
) {
  throw 'Google Web client ID must be a non-empty single-line string.'
}
if (
  $clientSecret -isnot [string] -or
  [string]::IsNullOrWhiteSpace($clientSecret) -or
  $clientSecret -match '[\r\n\0]'
) {
  throw 'Google Web client secret must be a non-empty single-line string.'
}
$callback = 'https://localhost:8443/api/v1/auth/callback'
$redirectUris = @($web.redirect_uris)
if (
  $redirectUris.Count -eq 0 -or
  @($redirectUris | Where-Object { $_ -is [string] -and $_ -ceq $callback }).Count -eq 0
) {
  throw "Google Web client must allow the exact callback: $callback"
}

$configurationPath = Join-Path $configurationDirectory 'hosted-local.env'
$secretPath = Join-Path $secretDirectory 'google_client_secret'
Assert-RegularReadableFile -Path $configurationPath -Label 'Hosted-local configuration'
if (Test-Path -LiteralPath $secretPath) {
  Assert-RegularReadableFile -Path $secretPath -Label 'Google client secret'
}

$configurationLines = @(
  Get-Content -LiteralPath $configurationPath |
    Where-Object { $_ -notmatch '^DASHBOARD_OIDC_CLIENT_ID=' }
)
$newConfiguration = @(
  $configurationLines
  "DASHBOARD_OIDC_CLIENT_ID=$clientId"
) -join "`n"
$newConfiguration += "`n"

$transactionId = [Guid]::NewGuid().ToString('N')
$stagedConfiguration = Join-Path $configurationDirectory ".hosted-local.env.$transactionId.staged"
$stagedSecret = Join-Path $secretDirectory ".google_client_secret.$transactionId.staged"
$configurationBackup = Join-Path $configurationDirectory ".hosted-local.env.$transactionId.backup"
$secretBackup = Join-Path $secretDirectory ".google_client_secret.$transactionId.backup"
$secretOriginallyExisted = Test-Path -LiteralPath $secretPath
$configurationReplaced = $false
$secretReplaced = $false
$transactionSucceeded = $false
$rollbackSucceeded = $false

function Install-StagedFile {
  param(
    [Parameter(Mandatory = $true)][string]$StagedPath,
    [Parameter(Mandatory = $true)][string]$DestinationPath
  )

  $discard = "$DestinationPath.$([Guid]::NewGuid().ToString('N')).discard"
  try {
    if (Test-Path -LiteralPath $DestinationPath) {
      [IO.File]::Replace($StagedPath, $DestinationPath, $discard)
    } else {
      [IO.File]::Move($StagedPath, $DestinationPath)
    }
  } finally {
    if (Test-Path -LiteralPath $discard) {
      Remove-Item -LiteralPath $discard -Force
    }
  }
}

function Restore-BackupFile {
  param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$DestinationPath
  )

  $discard = "$DestinationPath.$([Guid]::NewGuid().ToString('N')).discard"
  try {
    if (Test-Path -LiteralPath $DestinationPath) {
      [IO.File]::Replace($BackupPath, $DestinationPath, $discard)
    } else {
      [IO.File]::Move($BackupPath, $DestinationPath)
    }
  } finally {
    if (Test-Path -LiteralPath $discard) {
      Remove-Item -LiteralPath $discard -Force
    }
  }
}

try {
  Write-AtomicUtf8File -Path $stagedConfiguration -Value $newConfiguration
  Write-AtomicUtf8File -Path $stagedSecret -Value $clientSecret
  Protect-HostedLocalPath -Path $stagedConfiguration
  Protect-HostedLocalPath -Path $stagedSecret

  [IO.File]::Copy($configurationPath, $configurationBackup, $false)
  Protect-HostedLocalPath -Path $configurationBackup
  if ($secretOriginallyExisted) {
    [IO.File]::Copy($secretPath, $secretBackup, $false)
    Protect-HostedLocalPath -Path $secretBackup
  }

  Install-StagedFile -StagedPath $stagedSecret -DestinationPath $secretPath
  $secretReplaced = $true
  Install-StagedFile -StagedPath $stagedConfiguration -DestinationPath $configurationPath
  $configurationReplaced = $true
  Protect-HostedLocalPath -Path $secretPath
  Protect-HostedLocalPath -Path $configurationPath
  $transactionSucceeded = $true
} catch {
  $failureType = $_.Exception.GetType().Name
  if ($null -ne $_.Exception.InnerException) {
    $failureType += '/' + $_.Exception.InnerException.GetType().Name
  }
  try {
    if ($configurationReplaced) {
      Restore-BackupFile `
        -BackupPath $configurationBackup `
        -DestinationPath $configurationPath
    }
    if ($secretReplaced) {
      if ($secretOriginallyExisted) {
        Restore-BackupFile -BackupPath $secretBackup -DestinationPath $secretPath
      } elseif (Test-Path -LiteralPath $secretPath) {
        Remove-Item -LiteralPath $secretPath -Force
      }
    }
    if (Test-Path -LiteralPath $configurationPath) {
      Protect-HostedLocalPath -Path $configurationPath
    }
    if (Test-Path -LiteralPath $secretPath) {
      Protect-HostedLocalPath -Path $secretPath
    }
    $rollbackSucceeded = $true
  } catch {
    throw 'Google client configuration failed and rollback could not be completed.'
  }
  throw "Google client configuration failed ($failureType); previous configuration was restored."
} finally {
  foreach ($stagedPath in @($stagedConfiguration, $stagedSecret)) {
    if (Test-Path -LiteralPath $stagedPath) {
      Remove-Item -LiteralPath $stagedPath -Force
    }
  }
  if ($transactionSucceeded -or $rollbackSucceeded) {
    foreach ($backupPath in @($configurationBackup, $secretBackup)) {
      if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Force
      }
    }
  }
}

Write-Output "Imported Google Web client ID: $clientId"
Write-Output "Validated callback: $callback"
