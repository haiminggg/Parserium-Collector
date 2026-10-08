function Get-HostedLocalRepositoryRoot {
  [CmdletBinding()]
  param()

  $libraryPath = $MyInvocation.MyCommand.ScriptBlock.File
  if ([string]::IsNullOrWhiteSpace($libraryPath)) {
    throw 'Could not resolve the hosted-local common library path.'
  }
  return [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $libraryPath) '..\..'))
}

function Resolve-HostedLocalStateRoot {
  [CmdletBinding()]
  param(
    [string]$StateRoot,
    [string]$RepositoryRoot = (Get-HostedLocalRepositoryRoot)
  )

  $resolvedRepository = [IO.Path]::GetFullPath($RepositoryRoot)
  if ([string]::IsNullOrWhiteSpace($StateRoot)) {
    return [IO.Path]::GetFullPath("${resolvedRepository}.local\hosted-local")
  }
  return [IO.Path]::GetFullPath($StateRoot)
}

function Assert-NoReparsePointInPath {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  $current = [IO.Path]::GetFullPath($Path)
  while (-not [string]::IsNullOrWhiteSpace($current)) {
    if (Test-Path -LiteralPath $current) {
      $item = Get-Item -LiteralPath $current -Force
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Hosted-local paths cannot traverse a reparse point: $current"
      }
    }
    $parent = [IO.Directory]::GetParent($current)
    if ($null -eq $parent) {
      break
    }
    $current = $parent.FullName
  }
}

function Assert-SafeHostedLocalStateRoot {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot
  )

  if ([string]::IsNullOrWhiteSpace($StateRoot)) {
    throw 'Hosted-local state root cannot be empty.'
  }
  $resolvedState = [IO.Path]::GetFullPath($StateRoot).TrimEnd('\', '/')
  $resolvedRepository = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
  $filesystemRoot = [IO.Path]::GetPathRoot($resolvedState).TrimEnd('\', '/')
  if ($resolvedState.Equals($filesystemRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Hosted-local state root cannot be a filesystem root: $resolvedState"
  }

  $profile = $env:USERPROFILE
  if (-not [string]::IsNullOrWhiteSpace($profile)) {
    $resolvedProfile = [IO.Path]::GetFullPath($profile).TrimEnd('\', '/')
    if ($resolvedState.Equals($resolvedProfile, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Hosted-local state root cannot be the user profile: $resolvedState"
    }
  }

  $repositoryPrefix = $resolvedRepository + [IO.Path]::DirectorySeparatorChar
  if (
    $resolvedState.Equals($resolvedRepository, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedState.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)
  ) {
    throw "Hosted-local state root cannot be inside the repository: $resolvedState"
  }

  if (Test-Path -LiteralPath $resolvedState) {
    $item = Get-Item -LiteralPath $resolvedState -Force
    if (-not $item.PSIsContainer) {
      throw "Hosted-local state root exists but is not a directory: $resolvedState"
    }
  }
  Assert-NoReparsePointInPath -Path $resolvedState
}

function Assert-RegularReadableFile {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Label = 'File'
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "$Label does not exist: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "$Label is not a regular file: $Path"
  }
  $stream = $null
  try {
    $stream = [IO.File]::Open(
      $item.FullName,
      [IO.FileMode]::Open,
      [IO.FileAccess]::Read,
      [IO.FileShare]::Read
    )
  } catch {
    throw "$Label is not readable: $Path"
  } finally {
    if ($null -ne $stream) {
      $stream.Dispose()
    }
  }
}

function New-RandomBase64UrlSecret {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][ValidateRange(16, 4096)][int]$Length)

  $byteCount = [Math]::Ceiling(($Length * 3) / 4)
  $bytes = New-Object byte[] $byteCount
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
  } finally {
    $generator.Dispose()
  }
  $encoded = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  return $encoded.Substring(0, $Length)
}

function New-HostedLocalCredentialWrappingKey {
  [CmdletBinding()]
  param()

  $bytes = New-Object byte[] 32
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
  } finally {
    $generator.Dispose()
  }
  return [Convert]::ToBase64String($bytes)
}

function Assert-HostedLocalCredentialWrappingKey {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  Assert-RegularReadableFile -Path $Path -Label 'Firecrawl credential wrapping key secret'
  $encoded = [IO.File]::ReadAllText($Path)
  try {
    $decoded = [Convert]::FromBase64String($encoded)
  } catch {
    throw 'Firecrawl credential wrapping key must be canonical base64.'
  }
  if (
    $decoded.Length -ne 32 -or
    [Convert]::ToBase64String($decoded) -cne $encoded
  ) {
    throw 'Firecrawl credential wrapping key must contain one canonical 256-bit key.'
  }
}

function Write-AtomicUtf8File {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
  )

  $resolvedPath = [IO.Path]::GetFullPath($Path)
  $parent = Split-Path -Parent $resolvedPath
  if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Atomic write parent directory does not exist: $parent"
  }
  Assert-NoReparsePointInPath -Path $parent
  if (Test-Path -LiteralPath $resolvedPath) {
    $existing = Get-Item -LiteralPath $resolvedPath -Force
    if ($existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Atomic write destination is not a regular file: $resolvedPath"
    }
  }

  $temporary = Join-Path $parent ('.{0}.{1}.tmp' -f (
      [IO.Path]::GetFileName($resolvedPath),
      [Guid]::NewGuid().ToString('N')
    ))
  $replacementBackup = Join-Path $parent ('.{0}.{1}.replace-backup' -f (
      [IO.Path]::GetFileName($resolvedPath),
      [Guid]::NewGuid().ToString('N')
    ))
  $stream = $null
  $writer = $null
  try {
    $stream = [IO.FileStream]::new(
      $temporary,
      [IO.FileMode]::CreateNew,
      [IO.FileAccess]::Write,
      [IO.FileShare]::None
    )
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    $writer.Write($Value)
    $writer.Flush()
    $stream.Flush($true)
    $writer.Dispose()
    $writer = $null
    $stream = $null

    if (Test-Path -LiteralPath $resolvedPath) {
      [IO.File]::Replace($temporary, $resolvedPath, $replacementBackup)
    } else {
      [IO.File]::Move($temporary, $resolvedPath)
    }
  } finally {
    if ($null -ne $writer) {
      $writer.Dispose()
    } elseif ($null -ne $stream) {
      $stream.Dispose()
    }
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force
    }
    if (Test-Path -LiteralPath $replacementBackup) {
      Remove-Item -LiteralPath $replacementBackup -Force
    }
  }
}

function Protect-HostedLocalPath {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'Hosted-local ACL protection requires Windows.'
  }
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Cannot protect a missing hosted-local path: $Path"
  }
  Assert-NoReparsePointInPath -Path $Path
  $item = Get-Item -LiteralPath $Path -Force
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  $grant = if ($item.PSIsContainer) { "${identity}:(OI)(CI)(F)" } else { "${identity}:(F)" }

  & icacls.exe $item.FullName /reset | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not reset the hosted-local ACL: $($item.FullName)"
  }
  & icacls.exe $item.FullName /grant:r $grant | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not grant the hosted-local owner ACL: $($item.FullName)"
  }
  & icacls.exe $item.FullName /inheritance:r | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not remove inherited hosted-local access: $($item.FullName)"
  }
}

function Set-HostedLocalPkiContainerOwner {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [Parameter(Mandatory = $true)][string]$PkiDirectoryName,
    [string]$Image = 'parserium-collector:hosted-local'
  )

  if ($PkiDirectoryName -notmatch '^pki(?:-staging-[a-f0-9]{32})?$') {
    throw "Hosted-local PKI directory name is invalid: $PkiDirectoryName"
  }
  $repository = Get-HostedLocalRepositoryRoot
  $resolvedStateRoot = [IO.Path]::GetFullPath($StateRoot).TrimEnd('\', '/')
  Assert-SafeHostedLocalStateRoot `
    -StateRoot $resolvedStateRoot `
    -RepositoryRoot $repository
  $pkiDirectory = Join-Path $resolvedStateRoot $PkiDirectoryName
  if (-not (Test-Path -LiteralPath $pkiDirectory -PathType Container)) {
    throw "Hosted-local PKI directory is missing: $pkiDirectory"
  }
  Assert-NoReparsePointInPath -Path $pkiDirectory

  $names = @(
    'ca-key.pem',
    'ca.pem',
    'minio-cert.pem',
    'minio-key.pem',
    'parserium-cert.pem',
    'parserium-key.pem'
  )
  foreach ($name in $names) {
    Assert-RegularReadableFile `
      -Path (Join-Path $pkiDirectory $name) `
      -Label "Hosted-local PKI file $name"
  }

  $mount = $resolvedStateRoot.Replace('\', '/') + ':/host-state'
  $targets = @($names | ForEach-Object { "/host-state/$PkiDirectoryName/$_" })
  & docker run --rm `
    --network none `
    --read-only `
    --cap-drop ALL `
    --cap-add CHOWN `
    --cap-add DAC_OVERRIDE `
    --security-opt no-new-privileges `
    --user 0:0 `
    --volume $mount `
    --entrypoint chown `
    $Image `
    0:0 @targets
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not normalize hosted-local PKI container ownership.'
  }
}

function Get-HostedLocalComposeArguments {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$StateRoot
  )

  return @(
    '--project-name', 'parserium-hosted-local',
    '--env-file', (Join-Path $StateRoot 'config\hosted-local.env'),
    '-f', (Join-Path $RepositoryRoot 'deploy\hosted-local\compose.yaml')
  )
}

function Invoke-HostedLocalCompose {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [Parameter(Mandatory = $true)][string[]]$Arguments
  )

  $previousStateRoot = $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT
  $previousConfigFile = $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE
  try {
    $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $StateRoot.Replace('\', '/')
    $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = (
      Join-Path $StateRoot 'config\hosted-local.env'
    ).Replace('\', '/')
    $composeArguments = Get-HostedLocalComposeArguments `
      -RepositoryRoot $RepositoryRoot `
      -StateRoot $StateRoot
    & docker compose @composeArguments @Arguments
    if ($LASTEXITCODE -ne 0) {
      throw "Docker Compose failed with exit code $LASTEXITCODE."
    }
  } finally {
    $env:PARSERIUM_HOSTED_LOCAL_STATE_ROOT = $previousStateRoot
    $env:PARSERIUM_HOSTED_LOCAL_CONFIG_FILE = $previousConfigFile
  }
}

function Test-TcpPortAvailable {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][ValidateRange(1, 65535)][int]$Port)

  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
  try {
    $listener.Start()
    return $true
  } catch [Net.Sockets.SocketException] {
    return $false
  } finally {
    $listener.Stop()
  }
}

function Get-FileSha256 {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][string]$Path)

  Assert-RegularReadableFile -Path $Path -Label 'Hash input file'
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}
