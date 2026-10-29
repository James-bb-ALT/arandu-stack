[CmdletBinding()]
param([switch]$WithDetector)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Execute scripts/windows/Setup.ps1 primeiro.' }
$oldValue = [Environment]::GetEnvironmentVariable('ARANDU_TEST_DETECTOR')
Push-Location $projectRoot
try {
    $env:ARANDU_TEST_DETECTOR = if ($WithDetector) { '1' } else { '0' }
    & $pythonPath -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Testes Python falharam.' }
    & (Join-Path $projectRoot 'android/Test-Installer.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Testes do instalador Android falharam.' }
} finally {
    [Environment]::SetEnvironmentVariable('ARANDU_TEST_DETECTOR', $oldValue)
    Pop-Location
}
