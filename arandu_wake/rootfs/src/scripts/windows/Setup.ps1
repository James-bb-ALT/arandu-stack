[CmdletBinding()]
param([switch]$WithDetector)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $projectRoot
try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Instale Python 3.10 ou superior.' }
    & python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 ou superior obrigatorio.' }
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar ambiente virtual.' }
    }
    & $pythonPath -m pip install -e . -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependencias de diagnostico.' }
    if ($WithDetector) {
        & $pythonPath -m pip install -r requirements-detector.txt
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar detector. Consulte suporte de wheels para esta versao Python/arquitetura.' }
    }
    & $pythonPath -m arandu_wake validate
    if ($LASTEXITCODE -ne 0) { throw 'Inventario exemplo invalido.' }
    Write-Output 'Ambiente pronto. Nenhum dispositivo ou configuracao do Home Assistant foi alterado.'
} finally { Pop-Location }
