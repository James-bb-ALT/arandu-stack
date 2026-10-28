[CmdletBinding()]
param([switch]$DownloadModels, [switch]$CpuOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        py -3.10 -m venv .venv
        if ($LASTEXITCODE) { throw 'Python 3.10 necessario.' }
    }
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $torchIndex = 'https://download.pytorch.org/whl/cu128'
    if ($CpuOnly) { $torchIndex = 'https://download.pytorch.org/whl/cpu' }
    & $pythonPath -m pip install --index-url $torchIndex torch==2.10.0 torchaudio==2.10.0
    if ($LASTEXITCODE) { throw 'Falha ao instalar PyTorch isolado.' }
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE) { throw 'Falha nas dependencias.' }
    & $pythonPath -m pip check
    if ($LASTEXITCODE) { throw 'Dependencias inconsistentes.' }
    if ($DownloadModels) {
        & $pythonPath scripts/bootstrap_models.py
        if ($LASTEXITCODE) { throw 'Falha ao preparar modelos.' }
    }
} finally { Pop-Location }
