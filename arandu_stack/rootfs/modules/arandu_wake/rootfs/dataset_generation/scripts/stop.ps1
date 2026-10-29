$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Content -LiteralPath (Join-Path $projectRoot 'state\STOP_REQUESTED') -Value ([DateTime]::UtcNow.ToString('o')) -Encoding ascii
Write-Output 'Parada solicitada somente ao Arandu Wake. A sintese atual termina antes da parada; WAVs validos e checkpoints permanecem. O marcador impede retomada por logon ate resume.ps1.'
