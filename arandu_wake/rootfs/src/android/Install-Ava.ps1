[CmdletBinding()]
param(
    [ValidateSet('Download', 'Inspect', 'Install')][string]$Action = 'Download',
    [string]$Serial,
    [string]$Adb = 'adb',
    [string]$CacheDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'arandu-wake-android')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$release = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ava-release.json') -Raw | ConvertFrom-Json

function Invoke-AdbChecked([string[]]$Arguments) {
    $result = & $Adb @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ADB falhou: $($result -join [Environment]::NewLine)" }
    return ($result -join [Environment]::NewLine).Trim()
}

if ($Action -eq 'Inspect' -or $Action -eq 'Install') {
    if (-not (Get-Command $Adb -ErrorAction SilentlyContinue)) { throw 'Instale Android SDK Platform Tools e informe -Adb ou inclua adb no PATH.' }
    if ([string]::IsNullOrWhiteSpace($Serial) -or $Serial -notmatch '^[A-Za-z0-9._:-]+$') { throw 'Informe -Serial explicito (obtido com adb devices); nenhum aparelho e selecionado automaticamente.' }
    if ((Invoke-AdbChecked @('-s', $Serial, 'get-state')) -ne 'device') { throw 'Dispositivo indisponivel ou nao autorizado. Aceite a chave USB na tela.' }
    $api = [int](Invoke-AdbChecked @('-s', $Serial, 'shell', 'getprop', 'ro.build.version.sdk'))
    $model = Invoke-AdbChecked @('-s', $Serial, 'shell', 'getprop', 'ro.product.model')
    $abi = Invoke-AdbChecked @('-s', $Serial, 'shell', 'getprop', 'ro.product.cpu.abilist')
    Write-Output "Aparelho: $model; API $api; ABIs $abi"
    if ($api -lt $release.minimum_android_api) { throw 'Ava v0.6.2 exige Android 8/API 26 ou superior.' }
    if ($Action -eq 'Inspect') { return }
}

New-Item -ItemType Directory -Path $CacheDirectory -Force | Out-Null
$apk = Join-Path $CacheDirectory 'Ava-v0.6.2-release.apk'
if (-not (Test-Path -LiteralPath $apk)) {
    $partial = "$apk.partial"
    Invoke-WebRequest -Uri $release.url -OutFile $partial -UseBasicParsing
    $partialHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($partialHash -ne $release.sha256) { throw "SHA256 incorreto. Download preservado para diagnostico em $partial; nada foi instalado." }
    Move-Item -LiteralPath $partial -Destination $apk -Force
}
if ((Get-Item -LiteralPath $apk).Length -ne $release.size -or (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $release.sha256) {
    throw "APK em cache divergente: $apk. Nada foi instalado. Renomeie o arquivo antes de repetir."
}
Write-Output "APK verificado: $apk"
if ($Action -eq 'Download') { Write-Output 'Preparado. Nenhum aparelho foi acessado.'; return }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($apk)
try {
    $apkAbis = @($archive.Entries | Where-Object { $_.FullName -match '^lib/([^/]+)/libmicrofeatures\.so$' } | ForEach-Object { ($_.FullName -split '/')[1] })
    if (-not @($abi.Split(',') | Where-Object { $_ -in $apkAbis }).Count) { throw "ABI do aparelho incompativel com APK: $abi" }
    if (-not ($archive.GetEntry('assets/wakeWords/alexa.json')) -or -not ($archive.GetEntry('assets/wakeWords/alexa.tflite'))) { throw 'Modelo Alexa ausente no APK; instalacao interrompida.' }
} finally { $archive.Dispose() }

# Updates retain app data. A conflicting signing key causes ADB to fail; never uninstall to bypass it.
$install = Invoke-AdbChecked @('-s', $Serial, 'install', '-r', $apk)
if ($install -notmatch '(?m)^Success\s*$') { throw "Instalacao nao confirmada: $install" }
Write-Output 'Ava instalado. Abra Ava e conceda microfone/notificacoes na tela. Selecione somente Alexa, Auto start e nome exclusivo. Adicione ESPHome no HA e selecione o pipeline Arandu. Nenhum token ou permissao foi configurado por ADB.'
