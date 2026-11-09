"""UI-side Windows integration: opening folders in Explorer and crisp fonts on high-DPI screens."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opens Explorer on our own output folder
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)  # Finder
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def enable_high_dpi() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
