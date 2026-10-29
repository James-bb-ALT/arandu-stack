[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$assistantRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$assistantName = 'arandu-assistant-local'
$assistantImage = 'arandu-assistant:0.2.0'
$assistantExisting = & docker ps -a --filter "name=^/$assistantName$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Inicie o Docker Desktop antes de abrir o Arandu Assistant.' }
if ($assistantExisting -contains $assistantName) {
    $assistantInfo = & docker inspect $assistantName | ConvertFrom-Json
    $assistantOwner = $assistantInfo[0].Config.Labels.'io.arandu.managed'
    if ($assistantOwner -ne 'assistant-local') { throw 'Nome de container ocupado por outro aplicativo; preservado.' }
    & docker start $assistantName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao iniciar o container existente.' }
    Write-Output 'Arandu Assistant: http://127.0.0.1:8770'
    return
}
if (Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue) { throw 'Porta 8770 ocupada; nenhum processo foi encerrado.' }
$assistantImages = & docker image ls --format '{{.Repository}}:{{.Tag}}'
if ($assistantImages -notcontains $assistantImage) {
    & docker build -t $assistantImage (Join-Path $assistantRoot 'arandu_stack')
    if ($LASTEXITCODE -ne 0) { throw 'Falha no build do Arandu Assistant.' }
}
$assistantData = Join-Path $assistantRoot '.local-data'
New-Item -ItemType Directory -Force -Path $assistantData | Out-Null
$assistantArgs = @('run','-d','--name',$assistantName,'--label','io.arandu.managed=assistant-local','--env','ARANDU_LOCAL_CONTAINER=1','-p','127.0.0.1:8770:8099','--mount',"type=bind,source=$assistantData,target=/data")
$assistantCache = Join-Path $env:USERPROFILE '.cache\arandu-ai-lab'
foreach ($assistantModel in @(@{Folder='e5-small';Module='decision'},@{Folder='tucano';Module='llm'})) {
    $assistantWeights = Join-Path $assistantCache $assistantModel.Folder
    if (Test-Path -LiteralPath $assistantWeights) { $assistantArgs += @('--mount',"type=bind,source=$assistantWeights,target=/data/modules/$($assistantModel.Module)/models/$($assistantModel.Folder),readonly") }
}
$assistantArgs += $assistantImage
& docker @assistantArgs | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Falha ao iniciar o Arandu Assistant.' }
Write-Output 'Arandu Assistant: http://127.0.0.1:8770'
