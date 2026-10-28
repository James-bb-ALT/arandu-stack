param(
    [int]$Port = 8765,
    [string]$Experiment = 'runs/jaime_continued_20261014_human_balanced',
    [string]$BaselineModel = 'runs/rtx3080_20261013/export/fp32'
)
$ErrorActionPreference = 'Stop'
$comparisonRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$comparisonRun = (Resolve-Path -LiteralPath (Join-Path $comparisonRepo $Experiment)).Path
$comparisonBaseline = (Resolve-Path -LiteralPath (Join-Path $comparisonRepo $BaselineModel)).Path
New-Item -ItemType Directory -Force -Path $comparisonRun | Out-Null
try {
    $comparisonHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 2
    if ($comparisonHealth.dataset -and $comparisonHealth.experiment_path -eq $comparisonRun -and $comparisonHealth.baseline_model_path -eq $comparisonBaseline) {
        Write-Output "App disponível em http://127.0.0.1:$Port"
        exit 0
    }
} catch {}
$comparisonPython = (Get-Command python.exe).Source
Start-Process -FilePath $comparisonPython -ArgumentList @('tools/comparison_app/server.py', '--experiment', ('"{0}"' -f $comparisonRun), '--baseline-model', ('"{0}"' -f $comparisonBaseline), '--port', $Port) -WorkingDirectory $comparisonRepo -WindowStyle Hidden -RedirectStandardOutput (Join-Path $comparisonRun 'web.stdout.log') -RedirectStandardError (Join-Path $comparisonRun 'web.stderr.log') | Out-Null
Write-Output "App em inicialização: http://127.0.0.1:$Port"
