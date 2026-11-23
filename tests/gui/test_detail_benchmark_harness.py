from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for the benchmark harness")

from iPhoto.gui import detail_benchmark_harness as harness_module
from iPhoto.gui.detail_benchmark_harness import (
    PackagedDetailBenchmarkHarness,
    _BenchmarkItem,
)


def _snapshot(path: Path, generation: int) -> object:
    return SimpleNamespace(
        transaction=SimpleNamespace(
            generation=generation,
            source_identity=SimpleNamespace(path=path),
        )
    )


def test_rapid_switch_ignores_initial_presentation_until_final_transaction() -> None:
    initial = Path("/benchmark/a.jpg")
    middle = Path("/benchmark/b.jpg")
    harness = PackagedDetailBenchmarkHarness.__new__(PackagedDetailBenchmarkHarness)
    harness._active = _BenchmarkItem(
        path=initial,
        category="jpeg-hot",
        scenario="rapid-switch",
        switch_paths=(middle, initial),
    )
    harness._active_final_transaction = None
    harness._timeout = Mock()
    harness._run_post_present_scenario = Mock(return_value=0)

    with patch.object(harness_module.QTimer, "singleShot") as single_shot:
        # The initial A may be GPU-resident and present before B→A is dispatched.
        harness._on_presented(_snapshot(initial, 10))
        assert not harness._timeout.stop.called
        single_shot.assert_not_called()

        harness._active_final_transaction = (initial, 12)
        harness._on_presented(_snapshot(middle, 11))
        harness._on_presented(_snapshot(initial, 10))
        assert not harness._timeout.stop.called
        single_shot.assert_not_called()

        harness._on_presented(_snapshot(initial, 12))

    harness._timeout.stop.assert_called_once_with()
    harness._run_post_present_scenario.assert_called_once_with(harness._active)
    single_shot.assert_called_once_with(0, harness._complete_active)
