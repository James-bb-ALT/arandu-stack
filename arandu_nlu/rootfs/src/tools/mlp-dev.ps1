[CmdletBinding()]
param(
    [ValidateSet('check', 'build', 'corpus', 'corpus-check', 'versions', 'image', 'evaluation-check', 'evaluation-freeze', 'evaluation-run', 'evaluation-benchmark', 'evaluation-record')]
    [string]$Task = 'check'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$image = 'local-nlu-dev:rust-1.98.0'
$docker = Get-Command docker -ErrorAction Stop
& $docker.Source info --format '{{.OSType}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $docker.Source desktop start
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop in Linux-container mode, then rerun.' }
}
& $docker.Source build --tag $image --file (Join-Path $root 'tools/dev/Dockerfile') (Join-Path $root 'tools/dev')
if ($LASTEXITCODE -ne 0) { throw 'Developer image build failed.' }
if ($Task -eq 'image') {
    $versionLine = Get-Content (Join-Path $root 'addon/config.yaml') | Where-Object { $_ -match '^version: ' }
    $productVersion = ($versionLine -replace '^version: ', '').Trim('"', "'")
    if ($productVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid add-on version' }
    & $docker.Source build --network none --platform linux/amd64 --tag "local-nlu:$productVersion-amd64" --build-arg BUILD_ARCH=amd64 --build-arg "BUILD_VERSION=$productVersion" (Join-Path $root 'addon')
    if ($LASTEXITCODE -ne 0) { throw 'Add-on image build failed.' }
    exit 0
}
$output = Join-Path $root 'target'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$outputMount = if ($Task -eq 'evaluation-record') { "type=bind,source=$output,target=/output,readonly" } else { "type=bind,source=$output,target=/output" }
$mounts = @('--mount', "type=bind,source=$root,target=/source,readonly",
    '--mount', $outputMount)
if ($Task -eq 'corpus') {
    $mounts += @('--mount', "type=bind,source=$(Join-Path $root 'data/mlp'),target=/corpus-output")
}
if ($Task -eq 'evaluation-record') {
    $baseline = Join-Path $root 'evaluation/ptbr-independent/baselines'
    New-Item -ItemType Directory -Path $baseline -Force | Out-Null
    $mounts += @('--mount', "type=bind,source=$baseline,target=/baseline")
}
& $docker.Source run --rm --network none @mounts $image python3 /source/tools/dev/run.py $Task
if ($LASTEXITCODE -ne 0) { throw "MLP task '$Task' failed (exit $LASTEXITCODE)." }
