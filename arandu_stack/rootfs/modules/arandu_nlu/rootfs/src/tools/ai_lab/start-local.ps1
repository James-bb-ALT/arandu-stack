[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$labRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$labPython = Join-Path $labRoot '.venv-ai-lab-313\Scripts\python.exe'
$labLlama = Join-Path $env:LOCALAPPDATA 'AranduAILab\llama-b11954\llama-server.exe'
$labOptional = Join-Path $labRoot '..\start-optional-services.ps1'
if (Test-Path -LiteralPath $labOptional) { & $labOptional -Python $labPython }
if (!(Test-Path -LiteralPath $labPython) -or !(Test-Path -LiteralPath $labLlama)) { throw 'Prepare o ambiente e o llama-server conforme docs/ai-lab/README.md.' }
& docker info --format '{{.OSType}}' | Out-Null
if ($LASTEXITCODE -ne 0) { & docker desktop start }
if ($LASTEXITCODE -ne 0) { throw 'Docker indisponível para o NLU Rust.' }
$labContainers = & docker ps --format '{{.Names}}'
if ($labContainers -notcontains 'arandu-ai-lab-local-nlu') {
    $labBinary = Join-Path $labRoot 'target\release\local-nlu'
    if (!(Test-Path -LiteralPath $labBinary)) { throw 'Compile o NLU antes de iniciar.' }
    & docker run -d --rm --name arandu-ai-lab-local-nlu -p 127.0.0.1:11555:11555 --mount "type=bind,source=$labBinary,target=/local-nlu,readonly" local-nlu-dev:rust-1.98.0 /local-nlu serve --listen 0.0.0.0:11555 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao iniciar o NLU; verifique a porta 11555.' }
}
function Confirm-LabListener([string]$File, [string]$Name, [int]$Port) {
    $labListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$labListener) { return $false }
    $labOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($labListener.OwningProcess)"
    $labExpectedScript = Join-Path $PSScriptRoot $File
    if (!$labOwner.CommandLine -or $labOwner.CommandLine.IndexOf($labExpectedScript, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw "Port $Port belongs to another process." }
    $labListener.OwningProcess | Set-Content -LiteralPath (Join-Path $labRoot "target\ai-lab-$Name.pid")
    return $true
}
function Start-LabProcess([string]$File, [string]$Name, [int]$Port, [string[]]$ExtraArguments) {
    $labArgs = @('-u', ('"' + (Join-Path $PSScriptRoot $File) + '"')) + $ExtraArguments
    $labChild = Start-Process -FilePath $labPython -ArgumentList $labArgs -WorkingDirectory $labRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $labRoot "target\ai-lab-$Name.log") -RedirectStandardError (Join-Path $labRoot "target\ai-lab-$Name.err.log") -PassThru
    for ($labAttempt = 0; $labAttempt -lt 50; $labAttempt++) {
        if (Confirm-LabListener $File $Name $Port) { return }
        if ($labChild.HasExited) { throw "AI Lab $Name failed; inspect target logs." }
        Start-Sleep -Milliseconds 200
    }
    throw "AI Lab $Name did not open port $Port."
}
if (!(Confirm-LabListener 'worker.py' 'worker' 11556)) {
    Start-LabProcess 'worker.py' 'worker' 11556 @('serve', '--llama-server', ('"' + $labLlama + '"'))
}
if (!(Confirm-LabListener 'local_app.py' 'web' 8766)) {
    Start-LabProcess 'local_app.py' 'web' 8766 @()
}
Write-Output 'AI Lab: http://127.0.0.1:8766'
Write-Output 'Inferencia real local; sem HA conectado, catalogo vazio e somente dry-run.'
