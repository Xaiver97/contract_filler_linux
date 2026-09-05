# -*- mode: python ; coding: utf-8 -*-
"""
供用电合同手写填充工具 · PyInstaller Linux 打包配置（麒麟 V11 / Debian 11）
入口 launcher.py，多模块工程，资源随 assets/fonts/config/samples 携带。
"""
import os
from pathlib import Path

ROOT = Path(os.environ.get("CF_ROOT", os.getcwd())).resolve()
LAUNCHER = ROOT / "launcher.py"

a = Analysis(
    [str(LAUNCHER)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "fonts"), "fonts"),
        (str(ROOT / "config"), "config"),
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "samples"), "samples"),
    ],
    hiddenimports=[
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "numpy",
        "openpyxl",
        "docx",
        "PIL",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="contract_filler",          # ASCII 名（Linux/shell/AppImage 更友好）
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                    # 打开控制台便于 CLI 冒烟排查日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="contract_filler_linux",    # onedir 目录名（ASCII）
)