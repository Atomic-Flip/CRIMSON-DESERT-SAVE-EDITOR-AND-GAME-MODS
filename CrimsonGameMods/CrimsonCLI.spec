# -*- mode: python ; coding: utf-8 -*-
# Standalone CLI build — no GUI, no PySide6, minimal size

a = Analysis(
    ['cli.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('crimson_rs', 'crimson_rs'),
        ('game_baselines', 'game_baselines'),
    ],
    hiddenimports=[
        'crimson_rs', 'crimson_rs.enums', 'crimson_rs.pack_mod',
        'cli_core',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PyQt5', 'PyQt6', 'tkinter',
        'matplotlib', 'numpy', 'scipy', 'PIL',
        'gui', 'gui.tabs', 'gui.main_window',
    ],
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
    name='CrimsonCLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='app_icon.ico',
)
