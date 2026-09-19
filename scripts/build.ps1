[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$IsccPath,
    [switch]$SkipQuality,
    [switch]$SkipGuiSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Создайте .venv и установите проект с extra dev.' }
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
if (-not $IsccPath) {
    $isccCandidates = @((Join-Path $projectRoot 'build\tools\InnoSetup\ISCC.exe'))
    if (${env:ProgramFiles(x86)}) { $isccCandidates += (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe') }
    $isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($isccCommand) { $isccCandidates += $isccCommand.Source }
    $IsccPath = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Inno Setup 6.7.1 compiler не найден. Установите проверенный официальный Inno Setup в build\tools\InnoSetup либо задайте -IsccPath.'
}
$IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path
$isccVersion = (Get-Item -LiteralPath $IsccPath).VersionInfo.ProductVersion
# Official 6.7.1 compiler binaries contain 0.0.0.0 version resources. The setup
# uninstaller records the installed release; the .iss also checks ISPP's VER.
if (-not $isccVersion -or $isccVersion -eq '0.0.0.0') {
    $innoUninstaller = Join-Path ([System.IO.Path]::GetDirectoryName($IsccPath)) 'unins000.exe'
    if (Test-Path -LiteralPath $innoUninstaller -PathType Leaf) {
        $isccVersion = (Get-Item -LiteralPath $innoUninstaller).VersionInfo.ProductVersion.Trim()
    }
}
if ($isccVersion -notmatch '^6\.7\.1(?:\D|$)') { throw "Ожидается Inno Setup 6.7.1; найден $isccVersion." }

function Invoke-FrozenCheck {
    param([string]$Executable, [string]$Mode, [string]$ReportPath)
    if (Test-Path -LiteralPath $ReportPath) { throw "Путь отчёта уже существует: $ReportPath" }
    $arguments = @($Mode, '--report', ('"' + $ReportPath + '"'))
    if ($Mode -eq '--smoke-gui') {
        $profilePath = Join-Path ([System.IO.Path]::GetDirectoryName($ReportPath)) 'gui-smoke-profile'
        $arguments += @('--data-dir', ('"' + $profilePath + '"'))
    }
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(60000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Frozen check timeout: $Mode"
    }
    if ($process.ExitCode -ne 0) { throw "Frozen check $Mode exited $($process.ExitCode)." }
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) { throw "Frozen check $Mode не создал JSON report." }
    $report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $report) { throw "Пустой JSON report: $ReportPath" }
    if ($report.ok -ne $true -or $report.frozen -ne $true -or $report.cpu_provider -ne $true) {
        throw "Frozen check $Mode не подтвердил ok/frozen/CPU provider. См. $ReportPath"
    }
    if ($Mode -eq '--smoke-gui' -and $report.gui_visible -ne $true) { throw 'GUI smoke не подтвердил видимость окна.' }
    return $report
}

$originalPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\pyinstaller-cache'
Push-Location $projectRoot
try {
    if (-not $SkipQuality) { & (Join-Path $PSScriptRoot 'quality.ps1') -PythonPath $PythonPath }
    # Windows PowerShell 5.1 снимает вложенные двойные кавычки при передаче аргумента
    # нативной программе, поэтому python-код использует только удвоенные одинарные.
    & $PythonPath -c 'import struct,sys; assert sys.version_info[:2] == (3,13); assert struct.calcsize(''P'') == 8'
    if ($LASTEXITCODE -ne 0) { throw 'Сборка требует Python 3.13 x64.' }
    $appVersion = (& $PythonPath -c 'from importlib.metadata import version; print(version(''voxturbo''))').Trim()
    if ($LASTEXITCODE -ne 0 -or $appVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Версия voxturbo должна быть numeric X.Y.Z.' }
    $releaseDirectory = Join-Path $projectRoot 'dist\installer'
    $runStamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
    $reportDirectory = Join-Path $projectRoot "build\release\$runStamp"
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    # Единый источник текста лицензии — LICENSE в корне. Inno Setup показывает
    # страницу лицензии только из .txt/.rtf, поэтому копия выкладывается в build.
    $licenseSource = Join-Path $projectRoot 'LICENSE'
    if (-not (Test-Path -LiteralPath $licenseSource -PathType Leaf)) { throw 'LICENSE отсутствует: сборка без текста лицензии запрещена.' }
    $licenseStage = Join-Path $projectRoot 'build\licenses\VoxTurbo'
    New-Item -ItemType Directory -Path $licenseStage -Force | Out-Null
    Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $licenseStage 'LICENSE.txt') -Force
    & $PythonPath -m pip freeze --all | Set-Content -LiteralPath (Join-Path $reportDirectory 'python-environment.txt') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось записать python-environment.' }
    & $PythonPath -m PyInstaller 'installer\VoxTurbo.spec' --noconfirm --clean --distpath (Join-Path $projectRoot 'dist') --workpath (Join-Path $projectRoot 'build\pyinstaller')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $bundleDirectory = Join-Path $projectRoot 'dist\VoxTurbo'
    $frozenExecutable = Join-Path $bundleDirectory 'VoxTurbo.exe'
    if (-not (Test-Path -LiteralPath $frozenExecutable -PathType Leaf)) { throw 'PyInstaller не создал VoxTurbo.exe.' }
    $selfTest = Invoke-FrozenCheck -Executable $frozenExecutable -Mode '--self-test' -ReportPath (Join-Path $reportDirectory 'frozen-self-test.json')
    $guiSmoke = $null
    if (-not $SkipGuiSmoke) {
        $guiSmoke = Invoke-FrozenCheck -Executable $frozenExecutable -Mode '--smoke-gui' -ReportPath (Join-Path $reportDirectory 'frozen-gui-smoke.json')
    }
    & $IsccPath /Qp ('/DAppVersion=' + $appVersion) ('/DSourceDir=' + $bundleDirectory) ('/DReleaseDir=' + $releaseDirectory) 'installer\VoxTurbo.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }
    $installerPath = Join-Path $releaseDirectory "VoxTurbo-Setup-$appVersion.exe"
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) { throw 'Inno Setup не создал установщик.' }
    $installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    ($installerHash + '  ' + [System.IO.Path]::GetFileName($installerPath)) | Set-Content -LiteralPath ($installerPath + '.sha256') -Encoding ASCII
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    $commit = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { $commit = 'unavailable' }
    $gitStatus = @(& git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { $gitStatus = @('unavailable') }
    $manifest = [ordered]@{
        version = $appVersion
        created_utc = [DateTime]::UtcNow.ToString('o')
        git_commit = "$commit".Trim()
        git_dirty = ($gitStatus.Count -gt 0)
        source_changes = $gitStatus
        target = 'Windows 10 22H2 x64, build 19045'
        windows_11 = 'DEFERRED v2'
        installer_path = $installerPath
        installer_bytes = (Get-Item -LiteralPath $installerPath).Length
        installer_sha256 = $installerHash
        authenticode_status = [string]$signature.Status
        inno_setup = $isccVersion
        quality = $(if ($SkipQuality) { 'NOT RUN' } else { 'PASS' })
        frozen_self_test = $selfTest
        frozen_gui_smoke = $(if ($SkipGuiSmoke) { 'NOT RUN' } else { $guiSmoke })
        clean_windows_10_vm = 'NOT RUN; separate R1 acceptance required'
        model_weights = 'Not bundled; user-triggered verified download required'
    }
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $reportDirectory 'build-manifest.json') -Encoding UTF8
    if ($signature.Status -ne 'Valid') { Write-Warning 'Проверочная сборка не подписана доверенной подписью; Windows может показать неизвестного издателя. Защиту Windows не отключать.' }
    Write-Output "Installer: $installerPath"
    Write-Output "SHA256: $installerHash"
    Write-Output "Evidence: $reportDirectory"
    Write-Output 'Clean Windows 10 VM acceptance: NOT RUN.'
}
finally {
    Pop-Location
    $env:PYINSTALLER_CONFIG_DIR = $originalPyInstallerConfig
}
