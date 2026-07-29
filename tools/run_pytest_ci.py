"""Run pytest without invoking unsafe native extension finalizers.

The Linux offscreen suite imports PySide6, Qt multimedia/RHI, PyAV, OpenGL,
Numba, and Torch in one process. Pytest and all fixtures finish successfully,
including explicit QApplication shutdown, but those native libraries may still
crash while CPython runs process-global interpreter-exit hooks.

This runner preserves pytest's real exit status. It skips interpreter
finalization only after pytest has returned and both output streams have been
flushed. Normal application shutdown remains covered by a separate subprocess
smoke test that does not use this runner.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

import pytest


def run_pytest(arguments: Sequence[str]) -> int:
    """Return pytest's exit status after its complete session teardown."""

    return int(pytest.main(list(arguments)))


def exit_without_native_finalizers(status: int) -> None:
    """Flush pytest output and terminate with ``status`` without C finalizers."""

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(status))


def main(arguments: Sequence[str] | None = None) -> None:
    resolved_arguments = sys.argv[1:] if arguments is None else arguments
    exit_without_native_finalizers(run_pytest(resolved_arguments))


if __name__ == "__main__":
    main()
