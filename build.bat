@echo off
rem Rebuilds dist\IDriveReader (Windows) and dist\IDriveReader-mac (macOS) on a PC with Python 3.11+.
rem Steps: create .venv, install PyInstaller, run tests, build, run the packaged self-test.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :fail
)
.venv\Scripts\python -m pip install --quiet pyinstaller==6.22.3 || goto :fail
.venv\Scripts\python -m unittest discover -s tests || goto :fail
.venv\Scripts\python -m PyInstaller --noconfirm --log-level WARN IDriveReader.spec || goto :fail
dist\IDriveReader\IDriveReader.exe --self-test || goto :fail
rmdir /s /q dist\IDriveReader\kayitlar
copy /y KULLANIM.txt dist\IDriveReader\KULLANIM.txt >nul
.venv\Scripts\python packaging\build_mac_bundle.py || goto :fail
echo.
echo Hazir: Windows icin dist\IDriveReader, Mac icin dist\IDriveReader-mac klasorunun tamamini USB bellege kopyalayin.
exit /b 0
:fail
echo.
echo HATA: derleme basarisiz.
exit /b 1
