from __future__ import annotations

import sys
from pathlib import Path


def main():
    # The watchdog must start without importing Qt, NumPy or audio libraries.
    if len(sys.argv) == 4 and sys.argv[1] == "--watchdog":
        from cs2music.ptt import watchdog
        watchdog(int(sys.argv[2]), Path(sys.argv[3]))
        return 0
    if "--self-check" in sys.argv:
        from cs2music.diagnostics import self_check
        return self_check()
    if "--gui-check" in sys.argv:
        from cs2music.diagnostics import gui_check
        return gui_check()
    from cs2music.gui import run
    return run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from cs2music.config import data_dir
        report = traceback.format_exc()
        try:
            destination = data_dir() / "startup-error.log"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(report, encoding="utf-8")
        except OSError:
            pass
        if "--gui-check" in sys.argv or "--self-check" in sys.argv:
            raise SystemExit(1)
        raise
