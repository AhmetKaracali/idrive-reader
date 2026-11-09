"""PyInstaller entry script: opens the IDriveReader window (or runs ``--self-test``)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    if "--self-test" in sys.argv:
        from idrivereader.selftest import run_self_test
        return run_self_test()
    from idrivereader.gui.app import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
