# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the warehouse scan workstation."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
curl_datas, curl_binaries, curl_hiddenimports = collect_all("curl_cffi")

a = Analysis(
    [str(project_root / "scripts" / "warehouse_scan_desktop.py")],
    pathex=[str(project_root)],
    binaries=curl_binaries,
    datas=[*curl_datas],
    hiddenimports=[*curl_hiddenimports, "scripts.warehouse_scan_config"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="业财仓库扫码作业",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
