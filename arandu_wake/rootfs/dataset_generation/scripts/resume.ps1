[CmdletBinding()]
param([switch]$NoWait)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$marker = Join-Path $projectRoot 'state\STOP_REQUESTED'
if (Test-Path -LiteralPath $marker) {
    $statePath = Join-Path $projectRoot 'state\progress.json'
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $workerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($state.pid)" -ErrorAction SilentlyContinue
        if ($workerProcess -and $workerProcess.CommandLine -and $workerProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'background.py'))) {
            throw 'O worker ainda esta encerrando a sintese. Aguarde status.ps1 indicar running=false antes de retomar.'
        }
    }
}
if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker }
& (Join-Path $PSScriptRoot 'start-generation.ps1') -NoWait:$NoWait
