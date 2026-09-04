"""Lightweight packaged entry point that dispatches helpers before Qt imports."""

from __future__ import annotations

import sys


def main() -> int:
    arguments = sys.argv
    if len(arguments) > 1 and arguments[1] == "--startup-library-probe":
        from iPhoto.bootstrap.library_probe import _main as run_library_probe

        return run_library_probe(arguments[2:])

    from iPhoto.gui.main import main as run_gui

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
