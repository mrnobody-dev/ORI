# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


hiddenimports = [
    'api',
    'bech32',
    'block',
    'chain',
    'config',
    'crypto',
    'dns',
    'main',
    'mempool',
    'merkle',
    'node',
    'p2p',
    'pow',
    'seeder',
    'storage',
    'tx',
    'utils',
    'utxo',
    'wallet',
    'qrcode',
    'qrcode.image.pil',
    'PIL',
] + collect_submodules('qt')


a = Analysis(
    ['qt_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ORICore',
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ORICore',
)
