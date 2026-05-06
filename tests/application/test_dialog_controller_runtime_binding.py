from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.application.use_cases.scan_models import ScanMode
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.gui.ui.controllers.dialog_controller import DialogController


class _Library:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self.bind_calls: list[Path] = []
        self.scan_requests: list[tuple[Path, list[str], list[str]]] = []

    def root(self) -> Path | None:
        return self._root

    def bind_path(self, root: Path) -> None:
        self.bind_calls.append(root)
        self._root = root

    def is_scanning_path(self, _root: Path) -> bool:
        return False

    def start_scanning(
        self,
        root: Path,
        include: list[str],
        exclude: list[str],
    ) -> None:
        self.scan_requests.append((root, list(include), list(exclude)))


class _Context:
    def __init__(self, root: Path | None = None) -> None:
        self.library = _Library(root)
        self.facade = Mock()
        self.settings = Mock()
        self.open_calls: list[Path] = []

    def open_library(self, root: Path) -> object:
        self.open_calls.append(root)
        self.library._root = root
        return object()


def test_bind_library_dialog_uses_runtime_open_library(
    monkeypatch,
    tmp_path: Path,
) -> None:
    old_root = tmp_path / "old"
    old_root.mkdir()
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    context = _Context(old_root)
    status_bar = Mock()
    controller = DialogController(object(), context, status_bar)

    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.dialog_controller.dialogs.select_directory",
        lambda *_args, **_kwargs: selected_root,
    )

    assert controller.bind_library_dialog() == selected_root

    assert context.open_calls == [selected_root]
    assert context.library.bind_calls == []
    context.facade.cancel_active_scans.assert_called_once_with()
    context.settings.set.assert_called_once_with(
        "basic_library_path",
        str(selected_root),
    )
    context.facade.open_album.assert_called_once_with(selected_root)
    context.facade.scan_root_async.assert_called_once_with(
        selected_root,
        include=DEFAULT_INCLUDE,
        exclude=DEFAULT_EXCLUDE,
        mode=ScanMode.INITIAL_SAFE,
    )
    status_bar.showMessage.assert_called_once_with(
        f"Basic Library bound to {selected_root}"
    )
