# PyInstaller spec for JoensAutoDraw GUI (Windows)
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = Path(os.path.dirname(os.path.abspath(SPEC)))

ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
cv2_datas, cv2_binaries, cv2_hidden = collect_all("cv2")

added_datas = [
    (str(project_root / "examples" / "cat.png"), "examples"),
]

a = Analysis(
    ["launch_gui.py"],
    pathex=[str(project_root)],
    binaries=ctk_binaries + cv2_binaries,
    datas=ctk_datas + cv2_datas + added_datas,
    hiddenimports=ctk_hidden + cv2_hidden + ["PIL._tkinter_finder", "keyboard"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="JoensAutoDraw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
