[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$ExpectedSha256,
    [switch]$AllowInstallation,
    [switch]$SkipGuiSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $AllowInstallation) {
    throw 'Скрипт устанавливает и удаляет приложение в тестовом каталоге. Запустите с -AllowInstallation в выделенной тестовой среде.'
}
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) { throw 'Установщик не найден.' }
$actualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $ExpectedSha256) {
    $checksumFile = $InstallerPath + '.sha256'
    if (-not (Test-Path -LiteralPath $checksumFile -PathType Leaf)) { throw 'Требуется -ExpectedSha256 либо соседний .exe.sha256.' }
    $ExpectedSha256 = ((Get-Content -LiteralPath $checksumFile -Raw).Trim() -split '\s+')[0]
}
if ($ExpectedSha256 -notmatch '^[A-Fa-f0-9]{64}$' -or $actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
    throw 'SHA-256 установщика не совпадает с ожидаемым.'
}
$uninstallKey = '{6E4AEFCB-7FC4-4C94-96EE-B957B03D7C36}_is1'
$registryPaths = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$uninstallKey",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$uninstallKey",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$uninstallKey"
)
foreach ($registryPath in $registryPaths) {
    if (Test-Path -LiteralPath $registryPath) { throw "Обнаружена существующая установка VoxTurbo: $registryPath. Smoke не должен заменять её." }
}
if (Get-Process -Name VoxTurbo -ErrorAction SilentlyContinue) { throw 'VoxTurbo уже запущен. Проверка не будет закрывать чужой процесс.' }

$runStamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$smokeRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "build\install-smoke\$runStamp"))
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'build\install-smoke')) + [System.IO.Path]::DirectorySeparatorChar
if (-not $smokeRoot.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Некорректная граница тестового каталога.' }
# Reject existing junctions/symlinks before creating any descendant directory.
$ancestor = [System.IO.DirectoryInfo]::new($smokeRoot)
while ($null -ne $ancestor) {
    if ($ancestor.Exists -and (($ancestor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Reparse point запрещён в пути smoke: $($ancestor.FullName)"
    }
    if ($ancestor.FullName -eq $projectRoot) { break }
    $ancestor = $ancestor.Parent
}
if (Test-Path -LiteralPath $smokeRoot) { throw 'Smoke каталог уже существует; повторное использование запрещено.' }
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
$installDirectory = Join-Path $smokeRoot 'Application with spaces'
$evidenceDirectory = Join-Path $smokeRoot 'evidence'
$testProfile = Join-Path $smokeRoot 'LocalAppData'
New-Item -ItemType Directory -Path $evidenceDirectory, $testProfile | Out-Null
$installLog = Join-Path $evidenceDirectory 'install.log'
$uninstallLog = Join-Path $evidenceDirectory 'uninstall.log'
$installedExecutable = Join-Path $installDirectory 'VoxTurbo.exe'
$uninstaller = Join-Path $installDirectory 'unins000.exe'
$failure = $null
$installed = $false
$uninstalled = $false
$selfTest = $null
$guiSmoke = $null
$originalLocalAppData = $env:LOCALAPPDATA

function Invoke-SmokeProcess {
    param([string]$Executable, [string[]]$Arguments, [string]$Label)
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(60000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "$Label timeout (60s), stopped owned process $($process.Id)."
    }
    if ($process.ExitCode -ne 0) { throw "$Label exited $($process.ExitCode)." }
}

function Read-SmokeReport {
    param([string]$ReportPath)
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) { throw "Отсутствует report: $ReportPath" }
    $report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $report) { throw "Пустой JSON report: $ReportPath" }
    if ($report.ok -ne $true -or $report.frozen -ne $true -or $report.cpu_provider -ne $true) {
        throw "Installed check не подтвердил ok/frozen/CPU provider. См. $ReportPath"
    }
    return $report
}

try {
    $installArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/NOICONS', '/TASKS=""', ('/DIR="' + $installDirectory + '"'), ('/LOG="' + $installLog + '"'))
    Invoke-SmokeProcess -Executable $InstallerPath -Arguments $installArguments -Label 'Install'
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) { throw 'Установленный exe отсутствует.' }
    $installed = $true
    $registration = Get-ItemProperty -LiteralPath $registryPaths[0]
    $registeredLocation = [System.IO.Path]::GetFullPath($registration.InstallLocation).TrimEnd('\')
    if ($registeredLocation -ne $installDirectory.TrimEnd('\')) { throw 'Uninstall registration указывает за пределы smoke install.' }
    # Only application checks receive a disposable data profile. Installation and
    # uninstallation use the real per-user registry, guarded above against collision.
    $env:LOCALAPPDATA = $testProfile
    $selfTestReport = Join-Path $evidenceDirectory 'installed-self-test.json'
    Invoke-SmokeProcess -Executable $installedExecutable -Arguments @('--self-test', '--report', ('"' + $selfTestReport + '"')) -Label 'Installed self-test'
    $selfTest = Read-SmokeReport -ReportPath $selfTestReport
    if (-not $SkipGuiSmoke) {
        $guiReport = Join-Path $evidenceDirectory 'installed-gui-smoke.json'
        Invoke-SmokeProcess -Executable $installedExecutable -Arguments @('--smoke-gui', '--data-dir', ('"' + $testProfile + '"'), '--report', ('"' + $guiReport + '"')) -Label 'Installed GUI smoke'
        $guiSmoke = Read-SmokeReport -ReportPath $guiReport
        if ($guiSmoke.gui_visible -ne $true) { throw 'Installed GUI smoke не подтвердил видимость окна.' }
    }
}
catch { $failure = $_.Exception.Message }
finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        try {
            Invoke-SmokeProcess -Executable $uninstaller -Arguments @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + $uninstallLog + '"')) -Label 'Uninstall'
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            while ((Test-Path -LiteralPath $installedExecutable) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
            if (Test-Path -LiteralPath $installedExecutable) { throw 'Установленный exe остался после удаления.' }
            foreach ($registryPath in $registryPaths) {
                if (Test-Path -LiteralPath $registryPath) { throw "Uninstall registration осталась: $registryPath" }
            }
            $uninstalled = $true
        }
        catch {
            if ($failure) { $failure += ' Cleanup: ' + $_.Exception.Message }
            else { $failure = $_.Exception.Message }
        }
    }
    elseif ($installed) { $failure = 'Uninstaller отсутствует; проверить smoke каталог вручную.' }
    $result = [ordered]@{
        created_utc = [DateTime]::UtcNow.ToString('o')
        installer = $InstallerPath
        sha256 = $actualHash
        install_directory = $installDirectory
        host_windows = [Environment]::OSVersion.Version.ToString()
        target_acceptance = 'Windows 10 22H2 x64 build 19045 clean VM: NOT RUN by this local smoke'
        installed = $installed
        self_test = $selfTest
        gui_smoke = $(if ($SkipGuiSmoke) { 'NOT RUN' } else { $guiSmoke })
        uninstalled = $uninstalled
        result = $(if ($failure) { 'FAIL' } else { 'PASS' })
        error = $failure
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $evidenceDirectory 'smoke-install-report.json') -Encoding UTF8
    Write-Output "Smoke evidence: $evidenceDirectory"
}
if ($failure) { throw $failure }
Write-Output 'PASS: isolated install, installed executable checks, uninstall. Clean Windows 10 VM acceptance remains separate.'
