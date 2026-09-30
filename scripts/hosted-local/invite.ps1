param(
  [Parameter(Mandatory = $true)][string]$Email,
  [Parameter(Mandatory = $true)][string]$WorkspaceName,
  [ValidateRange(1, 168)][int]$ExpiresHours = 24,
  [string]$StateRoot
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'lifecycle.ps1')

try {
  $mailAddress = [Net.Mail.MailAddress]::new($Email)
} catch {
  throw 'Invitation email is invalid.'
}
if (-not [string]::Equals($mailAddress.Address, $Email.Trim(), [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Invitation email must contain only one bare email address.'
}
$normalizedWorkspace = $WorkspaceName.Trim()
if ($normalizedWorkspace.Length -lt 1 -or $normalizedWorkspace.Length -gt 120) {
  throw 'Workspace name must contain 1 through 120 characters.'
}

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot -StateRoot $resolvedStateRoot -RepositoryRoot $repository
Assert-RegularReadableFile `
  -Path (Join-Path $resolvedStateRoot 'config\hosted-local.env') `
  -Label 'Hosted-local configuration'
$health = Get-HostedLocalApiHealth `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot
if ($health -cne 'healthy') {
  throw "Hosted-local API must be healthy before creating an invitation. Current status: $health"
}

$output = @(Invoke-HostedLocalCompose `
    -RepositoryRoot $repository `
    -StateRoot $resolvedStateRoot `
    -Arguments @(
      'run', '--rm', '--no-deps', '-T', 'api',
      'python', '-m', 'parserium_collector.cli.invite', 'create',
      '--email', $mailAddress.Address,
      '--workspace-name', $normalizedWorkspace,
      '--role', 'owner',
      '--expires-hours', [string]$ExpiresHours
    ))
$urls = @($output | Where-Object { [string]$_ -match '^https://localhost:8443/#invite=[^\s]+$' })
if ($urls.Count -ne 1 -or $output.Count -ne 1) {
  throw 'Invitation CLI did not return exactly one hosted-local invitation URL.'
}
Write-Output "Sensitive, one-time invitation URL: $($urls[0])"
