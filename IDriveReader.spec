# PyInstaller build recipe:  .venv\Scripts\python -m PyInstaller --noconfirm IDriveReader.spec
# Produces dist\IDriveReader\ (IDriveReader.exe + _internal). Copy that whole folder to the USB stick.
a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    excludes=["unittest", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="IDriveReader",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="IDriveReader", upx=False)
