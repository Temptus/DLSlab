# server.spec
import os
block_cipher = None

# SPECPATH apunta al directorio del spec (build\).
# Usamos el directorio padre para referenciar el resto del proyecto.
_root = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(_root, 'ui', 'main_window.py')],  # Entry point real: GUI del profesor
    pathex=[_root],
    binaries=[],
    datas=[
        (os.path.join(_root, 'icon.png'), '.'),
        (os.path.join(_root, 'docs'), 'docs'),
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'mss',
        'PIL',
        'PIL.Image',
        'asyncio',
        'wakeonlan',
        'psutil',
        'qt_material',
        'qtawesome',
        'qtawesome.iconic_font',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pynput',   # no necesario en el servidor
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DLSlab_Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # Sin ventana de consola (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,      # El servidor NO requiere admin
    icon=os.path.join(_root, 'icon.ico'),
)
