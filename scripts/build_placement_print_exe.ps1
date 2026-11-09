$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot "venv\Scripts\python.exe"
$spec = Join-Path $PSScriptRoot "placement_printer.spec"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment was not found: $python"
}

Set-Location -LiteralPath $projectRoot

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -c "import PyInstaller" 2>$null
$pyInstallerMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = $previousErrorAction
if ($pyInstallerMissing) {
    Write-Host "Installing PyInstaller for the first build..."
    & $python -m pip install "PyInstaller>=6.16"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed" }
}

& $python -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) { throw "Print client build failed" }

$expectedOutput = Join-Path $projectRoot "dist\业财置货自动打印.exe"
if (-not (Test-Path -LiteralPath $expectedOutput)) {
    throw "Expected print client was not generated: $expectedOutput"
}
$output = Get-Item -LiteralPath $expectedOutput

Write-Host "Build completed: $($output.FullName)"
Write-Host "Copy the exe to the warehouse PC and double-click it. SumatraPDF and the Windows printer driver are still required."
