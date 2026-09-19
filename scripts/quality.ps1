[CmdletBinding()]
param(
    [string]$PythonPath,
    [switch]$IncludeWindowsTests,
    [switch]$IncludeModelTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python venv отсутствует. Выполните py -3.13 -m venv .venv и .venv\Scripts\python -m pip install -e ".[dev]".'
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$testFilter = @()
if (-not $IncludeWindowsTests) { $testFilter += 'not windows' }
if (-not $IncludeModelTests) { $testFilter += 'not model' }
if ($IncludeWindowsTests -and $env:VOXTURBO_ALLOW_INTERACTIVE_TESTS -ne '1') {
    throw 'Windows smoke требует VOXTURBO_ALLOW_INTERACTIVE_TESTS=1 и выделенного окна-приёмника.'
}

Push-Location $projectRoot
try {
    & $PythonPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'pip check завершился ошибкой.' }
    & $PythonPath -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Ruff lint завершился ошибкой.' }
    & $PythonPath -m ruff format --check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Ruff format --check завершился ошибкой.' }
    $pytestArguments = @('-m', 'pytest', 'tests', '--strict-markers')
    if ($testFilter.Count -gt 0) { $pytestArguments += @('-m', ($testFilter -join ' and ')) }
    & $PythonPath @pytestArguments
    if ($LASTEXITCODE -ne 0) { throw 'Pytest завершился ошибкой; пустой набор тестов не считается PASS.' }
    Write-Output 'PASS: pip check, Ruff, pytest. Windows/model tests включены только при явных switches.'
}
finally { Pop-Location }
