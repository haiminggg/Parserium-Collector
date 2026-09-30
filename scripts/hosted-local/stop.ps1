param([string]$StateRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$repository = Get-HostedLocalRepositoryRoot
$resolvedStateRoot = Resolve-HostedLocalStateRoot `
  -StateRoot $StateRoot `
  -RepositoryRoot $repository
Assert-SafeHostedLocalStateRoot -StateRoot $resolvedStateRoot -RepositoryRoot $repository
Assert-RegularReadableFile `
  -Path (Join-Path $resolvedStateRoot 'config\hosted-local.env') `
  -Label 'Hosted-local configuration'
Invoke-HostedLocalCompose `
  -RepositoryRoot $repository `
  -StateRoot $resolvedStateRoot `
  -Arguments @('stop')
Write-Output 'Stopped hosted-local containers. Database and object-storage volumes were preserved.'
