param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('api', 'worker', 'db', 'minio', 'migrate', 'storage-bootstrap')]
  [string]$Service,
  [ValidateRange(1, 2000)][int]$Tail = 100,
  [string]$StateRoot
)

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
  -Arguments @('logs', '--no-color', '--tail', [string]$Tail, $Service)
