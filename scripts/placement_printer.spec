# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the warehouse placement printer desktop client."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
curl_datas, curl_binaries, curl_hiddenimports = collect_all("curl_cffi")

a = Analysis(
    [str(project_root / "scripts" / "placement_print_desktop.py")],
    pathex=[str(project_root)],
    binaries=curl_binaries,
    datas=[
        (str(project_root / "assets" / "fonts" / "NotoSansSC.ttf"), "assets/fonts"),
        (str(project_root / "assets" / "fonts" / "NotoSansSC-Bold.ttf"), "assets/fonts"),
        (str(project_root / "assets" / "fonts" / "OFL.txt"), "assets/fonts"),
        (str(Path.home() / "AppData" / "Local" / "SumatraPDF" / "SumatraPDF.exe"), "SumatraPDF"),
        (str(Path.home() / "AppData" / "Local" / "SumatraPDF" / "libmupdf.dll"), "SumatraPDF"),
        *curl_datas,
    ],
    hiddenimports=[*curl_hiddenimports, "scripts.placement_print_agent", "scripts.preview_order_printing"],
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
    name="业财置货自动打印",
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
