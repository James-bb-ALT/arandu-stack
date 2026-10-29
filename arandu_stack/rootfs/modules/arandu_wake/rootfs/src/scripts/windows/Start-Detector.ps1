[CmdletBinding()]
param([ValidateRange(0.01,0.99)][double]$Threshold = 0.5, [ValidateRange(1,20)][int]$TriggerLevel = 1,
      [ValidateRange(1024,65535)][int]$Port = 10400, [switch]$DebugLogging)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '../../.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Execute Setup.ps1 -WithDetector primeiro.' }
$arguments = @('-m','wyoming_openwakeword','--uri',"tcp://127.0.0.1:$Port",'--threshold', $Threshold.ToString([Globalization.CultureInfo]::InvariantCulture),'--trigger-level', "$TriggerLevel")
if ($DebugLogging) { $arguments += '--debug' }
Write-Output 'Detector de desenvolvimento em loopback. Ctrl+C encerra. Para HAOS, use o aplicativo oficial.'
& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Detector encerrou com erro.' }
