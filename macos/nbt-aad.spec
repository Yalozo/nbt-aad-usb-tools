# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import runpy
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
version = runpy.run_path(str(root / 'windows/nbt_aad_pc/__init__.py'))['__version__']
a = Analysis(
    [str(root / 'windows/main.py')],
    pathex=[str(root / 'windows')],
    binaries=[],
    datas=[(str(root / 'nbt-aad.sh'), '.')] + copy_metadata('paramiko', recursive=True),
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='NBT-AAD-PC',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, target_arch=None, codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='NBT-AAD-PC')
app = BUNDLE(
    coll, name='NBT-AAD-PC.app', bundle_identifier='io.github.yalozo.nbt-aad-pc',
    version=version,
    info_plist={
        'CFBundleDisplayName': 'NBT-AAD PC',
        'CFBundleShortVersionString': version,
        'CFBundleVersion': version,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '13.0',
        'NSLocalNetworkUsageDescription':
            'Connect to your BMW or MINI NBT Evo head unit over a local ENET Ethernet connection.',
    },
)
