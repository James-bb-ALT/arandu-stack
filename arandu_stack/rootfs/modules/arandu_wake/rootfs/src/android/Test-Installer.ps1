# Safety tests. Uses a fake ADB executable; never enumerates or touches real devices.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$installer = Join-Path $PSScriptRoot 'Install-Ava.ps1'
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) ('arandu-wake-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
$fake = Join-Path $testDirectory 'adb-fixture.ps1'
@'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Parts)
$global:LASTEXITCODE = 0
if (($Parts -join ' ') -match '^-s (fixture|old) get-state$') { 'device' }
elseif (($Parts -join ' ') -eq '-s fixture shell getprop ro.build.version.sdk') { '33' }
elseif (($Parts -join ' ') -eq '-s old shell getprop ro.build.version.sdk') { '25' }
elseif (($Parts -join ' ') -match '^-s (fixture|old) shell getprop ro.product.model$') { 'SIMULATED_ONLY' }
elseif (($Parts -join ' ') -match '^-s (fixture|old) shell getprop ro.product.cpu.abilist$') { 'arm64-v8a' }
else { $global:LASTEXITCODE = 2; 'Unexpected fake ADB arguments; mutation denied.' }
'@ | Set-Content -LiteralPath $fake
function Expect-Failure([string]$Name, [scriptblock]$Run, [string]$Pattern) {
    $caught = $false
    try { & $Run | Out-Null } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        $caught = $true
    }
    if (-not $caught) { throw "FAILED $Name : expected rejection" }
    Write-Output "PASS $Name"
}
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$parseTokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw 'Installer parse failed' }
Write-Output 'PASS PowerShell syntax'
& $installer -Action Inspect -Adb $fake -Serial fixture | Out-Null
Write-Output 'PASS simulated supported Android inspection'
Expect-Failure 'explicit serial required' { & $installer -Action Inspect -Adb $fake } 'Serial explicito'
Expect-Failure 'reject unsupported Android' { & $installer -Action Inspect -Adb $fake -Serial old } 'API 26'
Set-Content -LiteralPath (Join-Path $testDirectory 'Ava-v0.6.2-release.apk') -Value 'invalid APK fixture'
Expect-Failure 'reject corrupt cached APK before install' { & $installer -Action Download -CacheDirectory $testDirectory } 'divergente'
Write-Output '5 checks passed. Fixtures remain in TEMP; no Android hardware tested.'
