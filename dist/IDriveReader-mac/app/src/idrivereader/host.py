"""Host integration that is not UI: where captures are saved and keeping the computer awake.
Works on Windows (the packaged .exe) and macOS (the .command bundle)."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002
_PROJECT_MARKER = "launcher.py"
HOME_OVERRIDE = "IDRIVEREADER_HOME"  # set by the macOS launcher to the bundle folder


def application_folder() -> Path:
    """Where ``kayitlar`` lives: next to IDriveReader.exe / IDriveReader.command, or the project root."""
    override = os.environ.get(HOME_OVERRIDE)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    for folder in here.parents:
        if (folder / _PROJECT_MARKER).exists():
            return folder
    return Path.cwd()


def resolve_output_root() -> Path:
    """Create (if needed) and return ``kayitlar`` next to the program, or a Documents fallback if that is read-only."""
    preferred = application_folder() / "kayitlar"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".yazma_testi"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        fallback = Path.home() / "Documents" / "IDriveReader" / "kayitlar"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@contextmanager
def prevent_sleep() -> Iterator[None]:
    """Keep the computer (and the screen) awake inside the ``with`` block. Nothing persists afterwards."""
    if sys.platform == "win32":
        set_state = ctypes.windll.kernel32.SetThreadExecutionState
        set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
        try:
            yield
        finally:
            set_state(_ES_CONTINUOUS)
        return
    if sys.platform == "darwin":
        # caffeinate exits on its own when this process ends (-w), so nothing can be left behind.
        keeper = subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            yield
        finally:
            keeper.terminate()
        return
    yield
