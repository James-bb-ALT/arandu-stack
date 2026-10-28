$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'runtime_status.py')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao ler estado do gerador.' }
