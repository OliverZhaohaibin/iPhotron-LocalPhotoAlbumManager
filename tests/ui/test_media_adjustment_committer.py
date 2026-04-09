from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for media adjustment committer tests",
    exc_type=ImportError,
)

from iPhoto.gui.ui.media import MediaAdjustmentCommitter


def test_commit_persists_adjustments_and_emits_signal() -> None:
    asset_vm = Mock()
    pause = Mock()
    resume = Mock()
    committer = MediaAdjustmentCommitter(
        asset_vm=asset_vm,
        pause_watcher=pause,
        resume_watcher=resume,
    )
    source = Path("/fake/photo.jpg")
    emitted: list[tuple[Path, str]] = []
    committer.adjustmentsCommitted.connect(lambda path, reason: emitted.append((path, reason)))

    with patch("iPhoto.gui.ui.media.media_adjustment_committer.sidecar.save_adjustments") as save_mock:
        assert committer.commit(source, {"Exposure": 0.2}, reason="edit_done") is True

    pause.assert_called_once_with()
    save_mock.assert_called_once_with(source, {"Exposure": 0.2})
    asset_vm.invalidate_thumbnail.assert_called_once_with(str(source))
    resume.assert_called_once_with()
    assert emitted == [(source, "edit_done")]


def test_commit_resumes_watcher_and_skips_signal_on_failure() -> None:
    asset_vm = Mock()
    pause = Mock()
    resume = Mock()
    committer = MediaAdjustmentCommitter(
        asset_vm=asset_vm,
        pause_watcher=pause,
        resume_watcher=resume,
    )
    source = Path("/fake/photo.jpg")
    emitted: list[tuple[Path, str]] = []
    committer.adjustmentsCommitted.connect(lambda path, reason: emitted.append((path, reason)))

    with patch(
        "iPhoto.gui.ui.media.media_adjustment_committer.sidecar.save_adjustments",
        side_effect=RuntimeError("boom"),
    ):
        assert committer.commit(source, {"Exposure": 0.2}, reason="edit_done") is False

    pause.assert_called_once_with()
    asset_vm.invalidate_thumbnail.assert_not_called()
    resume.assert_called_once_with()
    assert emitted == []
