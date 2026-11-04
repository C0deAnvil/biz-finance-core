$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot "venv\Scripts\python.exe"
$dist = Join-Path $projectRoot "dist"
$releaseRoot = Join-Path $projectRoot "release"
$releaseDir = Join-Path $releaseRoot "warehouse-desktop-tools-20261104"
$zipPath = "$releaseDir.zip"
$buildStarted = Get-Date
$specs = @(
    (Join-Path $PSScriptRoot "placement_printer.spec"),
    (Join-Path $PSScriptRoot "warehouse_scanner.spec")
)

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment was not found: $python"
}

$sumatraDir = Join-Path $env:USERPROFILE "AppData\Local\SumatraPDF"
foreach ($file in @("SumatraPDF.exe", "libmupdf.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $sumatraDir $file))) {
        throw "Installed SumatraPDF component was not found: $file"
    }
}

Set-Location -LiteralPath $projectRoot
& $python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is not installed in the project venv" }

foreach ($spec in $specs) {
    & $python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) { throw "Desktop client build failed: $spec" }
}

if (Test-Path -LiteralPath $releaseDir) {
    Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

$outputs = @(Get-ChildItem -LiteralPath $dist -Filter "*.exe" | Where-Object { $_.LastWriteTime -ge $buildStarted.AddSeconds(-2) })
if ($outputs.Count -ne 2) {
    throw "Expected exactly two freshly-built desktop executables, found $($outputs.Count)"
}
$outputs | Copy-Item -Destination $releaseDir
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\warehouse_desktop_tools_guide.md") -Destination $releaseDir

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
& $python -c "from pathlib import Path; import shutil; p=Path(r'$releaseDir'); shutil.make_archive(str(p), 'zip', root_dir=p.parent, base_dir=p.name)"
if ($LASTEXITCODE -ne 0) { throw "Warehouse bundle compression failed" }

$hashTargets = @($outputs | ForEach-Object { Join-Path $releaseDir $_.Name })
$hashTargets += $zipPath
Get-FileHash -LiteralPath $hashTargets -Algorithm SHA256 |
    Format-Table Path, Hash -AutoSize
Write-Host "Warehouse bundle completed: $zipPath"
