"""
Assemble dist/IDriveReader-mac: the same program for macOS, runnable by double-clicking
IDriveReader.command. Can be built on any OS (nothing is compiled):

    python packaging/build_mac_bundle.py

Contents: app/ (launcher + sources), runtime/ (relocatable CPython builds for Apple Silicon and
Intel from astral-sh/python-build-standalone, checksum-verified, Tk included), the .command
launcher and the Mac guide. The Python archives are downloaded once into build/mac_runtime.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CACHE = PROJECT / "build" / "mac_runtime"
TARGET = PROJECT / "dist" / "IDriveReader-mac"

RELEASE = "20261013"
PYTHON = "3.12.15"
BASE_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/"
ARCHIVES = [f"cpython-{PYTHON}+{RELEASE}-{arch}-apple-darwin-install_only.tar.gz"
            for arch in ("aarch64", "x86_64")]


def _download(name: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / name
    if not target.exists():
        print(f"indiriliyor: {name}")
        urllib.request.urlretrieve(BASE_URL + urllib.parse.quote(name), target)
    return target


def _verified(name: str, sums: dict) -> Path:
    path = _download(name)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if sums.get(name) != actual:
        path.unlink()
        raise SystemExit(f"SHA256 uyusmazligi: {name} (dosya silindi, tekrar calistirin)")
    return path


def _checksums() -> dict:
    sums = {}
    for line in _download("SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if name:
            sums[name.strip()] = digest.strip()
    return sums


def main() -> int:
    sums = _checksums()
    archives = [_verified(name, sums) for name in ARCHIVES]

    if TARGET.exists():
        shutil.rmtree(TARGET)
    (TARGET / "app").mkdir(parents=True)
    (TARGET / "runtime").mkdir()
    shutil.copy2(PROJECT / "launcher.py", TARGET / "app" / "launcher.py")
    shutil.copytree(PROJECT / "src", TARGET / "app" / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for archive in archives:
        shutil.copy2(archive, TARGET / "runtime" / archive.name)

    # Shell scripts must have Unix line endings to run on macOS.
    for source, name in ((PROJECT / "packaging" / "IDriveReader.command", "IDriveReader.command"),
                         (PROJECT / "KULLANIM-MAC.txt", "KULLANIM-MAC.txt")):
        text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
        (TARGET / name).write_bytes(text.encode("utf-8"))
    (TARGET / "IDriveReader.command").chmod(0o755)

    size = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file()) / 1e6
    print(f"Hazir: {TARGET} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
