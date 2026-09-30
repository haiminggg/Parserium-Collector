param(
  [Parameter(Mandatory = $true)][string]$ConfirmProject,
  [string]$StateRoot
)

$ErrorActionPreference = 'Stop'
if (-not [string]::Equals(
    $ConfirmProject,
    'parserium-hosted-local',
    [StringComparison]::Ordinal
  )) {
  throw 'Reset requires the exact confirmation: -ConfirmProject parserium-hosted-local'
}

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'lifecycle.ps1')

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot -StateRoot $resolvedStateRoot -RepositoryRoot $repository
Assert-RegularReadableFile `
  -Path (Join-Path $resolvedStateRoot 'config\hosted-local.env') `
  -Label 'Hosted-local configuration'
$resources = @(Get-HostedLocalProjectResources)
Assert-HostedLocalProjectLabels -Resources $resources
Invoke-HostedLocalCompose `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot `
  -Arguments @('down', '--volumes', '--remove-orphans')
Write-Output 'Reset hosted-local containers and volumes. External configuration, credentials, PKI, trust record, and archives were preserved.'
