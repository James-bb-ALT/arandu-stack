[CmdletBinding()]
param([switch]$NoWait, [ValidateRange(30,14400)][int]$ReadyTimeoutSeconds = 3600)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonwPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$background = Join-Path $projectRoot 'scripts\background.py'
$verifiedPath = Join-Path $projectRoot 'state\preflight_verified.json'
if (-not (Test-Path -LiteralPath $verifiedPath)) { throw 'Execute a sintese --preflight e verify_preflight.py antes de iniciar.' }
$verified = Get-Content -LiteralPath $verifiedPath -Raw | ConvertFrom-Json
if (-not $verified.verified -or -not $verified.resume_recovery_idempotent) { throw 'Preflight real/retomada nao confirmados.' }
if (Test-Path -LiteralPath (Join-Path $projectRoot 'state\STOP_REQUESTED')) { throw 'Parada solicitada. Use resume.ps1 para retomar explicitamente.' }
$statusPath = Join-Path $projectRoot 'state\progress.json'
if (Test-Path -LiteralPath $statusPath) {
    $old = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($old.pid)" -ErrorAction SilentlyContinue
    if ($oldProcess -and $oldProcess.CommandLine -and $oldProcess.CommandLine.Contains($background)) {
        Write-Output "Gerador proprio ja ativo: PID $($old.pid). Nenhum novo processo iniciado."
        return
    }
}
$runId = [Guid]::NewGuid().ToString('N')
$arguments = '"' + $background + '" --run-id ' + $runId
$sha = [Security.Cryptography.SHA256]::Create()
try { $suffix = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot)))).Replace('-','').Substring(0,8).ToLowerInvariant() }
finally { $sha.Dispose() }
$taskName = "AranduWakeDatasetGeneration_$suffix"
$mode = 'TaskScheduler'
$persistentId = $taskName
$taskReady = $false
try {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and -not (@($existing.Actions | Where-Object Arguments -Like "*$background*").Count)) { throw 'Nome de tarefa ocupado por outro projeto.' }
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $arguments -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Geracao independente de dataset Arandu Wake; nunca manipula Arandu TTS" -Force | Out-Null
    $taskReady = $true
} catch {
    Write-Output "Agendador indisponivel para este usuario; usando inicializacao por logon + processo independente. Motivo: $($_.Exception.Message)"
    $mode = 'UserStartup-WMI'
    $startup = [Environment]::GetFolderPath('Startup')
    $hook = Join-Path $startup "$taskName.cmd"
    $startScript = Join-Path $PSScriptRoot 'start-generation.ps1'
    $contents = '@echo off' + [Environment]::NewLine + 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $startScript + '" -NoWait' + [Environment]::NewLine
    if (Test-Path -LiteralPath $hook) {
        if ((Get-Content -LiteralPath $hook -Raw) -ne $contents) { throw 'Hook de inicializacao diferente ja existe; nao sera sobrescrito.' }
    } else { [IO.File]::WriteAllText($hook, $contents, [Text.Encoding]::ASCII) }
    $persistentId = "Startup:$taskName"
}
$launcher = @{mode=$mode; persistent_id=$persistentId; task_name=$taskName; run_id=$runId; project=$projectRoot; started_at=[DateTime]::UtcNow.ToString('o')}
[IO.File]::WriteAllText((Join-Path $projectRoot 'state\launcher.json'), ($launcher | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
if ($taskReady) {
    Start-ScheduledTask -TaskName $taskName
} else {
    # WMI cria o processo com pai no servico WMI, fora do job do terminal/agente.
    # pythonw não cria janela interativa.
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=('"' + $pythonwPath + '" ' + $arguments); CurrentDirectory=$projectRoot}
    if ($created.ReturnValue -ne 0) { throw "Falha ao criar processo independente: $($created.ReturnValue)" }
}
if ($NoWait) { Write-Output "Processo iniciado: $persistentId. Prontidao de audio ainda nao confirmada."; return }
$deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
while ([DateTime]::UtcNow -lt $deadline) {
    $answer = & $pythonPath (Join-Path $PSScriptRoot 'check_ready.py') --run-id $runId
    if ($LASTEXITCODE -eq 0) { Write-Output $answer; return }
    if ($LASTEXITCODE -ne 2) { throw 'Falha ao validar os WAVs de prontidao. Consulte logs; processo nao foi interrompido.' }
    if (Test-Path -LiteralPath $statusPath) {
        $progress = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        if ($progress.run_id -eq $runId) {
            if ($progress.stage -eq 'blocked') { throw "Gerador bloqueado: $($progress.error)" }
            Write-Output "Aguardando primeira producao persistente: $($progress.stage); PID $($progress.pid)"
        }
    }
    Start-Sleep -Seconds 10
}
throw 'Tempo de prontidao excedido. Processo preservado; consulte status.ps1. Nenhum sucesso foi declarado.'
