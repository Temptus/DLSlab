# agent.spec
import os
block_cipher = None

# SPECPATH apunta al directorio del spec (build\).
# Usamos el directorio padre para referenciar el resto del proyecto.
_root = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(_root, 'client', 'agent.py')],
    pathex=[_root],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'mss',
        'PIL',
        'PIL.Image',
        'pynput',
        'pynput.keyboard',
        'pynput.mouse',
        'psutil',
        'asyncio',
        'ctypes',
        'winreg',
        'configparser',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'wakeonlan',  # no necesario en el cliente
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
    name='DLSlab_Agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # Sin ventana de consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,       # El agente REQUIERE permisos de administrador
    icon=os.path.join(_root, 'icon.ico'),
)
