param(
    [int]$Limit = 500,
    [ValidateSet('cpu', 'cuda', 'auto')][string]$Device = 'cpu'
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $repository 'runs\jaime_robotico_500'
$scriptPath = Join-Path $PSScriptRoot 'generate_jaime_voicestudio.py'
$pythonPath = (Get-Command python -ErrorAction Stop).Source
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$generationArguments = @('-u', ('"{0}"' -f $scriptPath), '--limit', $Limit, '--device', $Device)
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$process = Start-Process -FilePath $pythonPath -ArgumentList $generationArguments `
    -WorkingDirectory $repository -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $outputDirectory "run_$runStamp.stdout.log") `
    -RedirectStandardError (Join-Path $outputDirectory "run_$runStamp.stderr.log")
@{
    pid = $process.Id
    started_at = (Get-Date).ToString('o')
    target = $Limit
    device = $Device
    script = $scriptPath
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputDirectory 'run_process.json') -Encoding utf8
Write-Output "Geração iniciada em segundo plano. PID: $($process.Id)"
Write-Output "Pasta: $outputDirectory"
Write-Output "Progresso: $(Join-Path $outputDirectory 'status.json')"
