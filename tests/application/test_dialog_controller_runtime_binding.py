from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.gui.ui.controllers.dialog_controller import DialogController


class _Library:
    def __init__(self, root: Path | None = None, scan_service: object | None = None) -> None:
        self._root = root
        self.scan_service = scan_service
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
    def __init__(self, root: Path | None = None, scan_service: object | None = None) -> None:
        self.library = _Library(root, scan_service)
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
    select_calls: list[dict[str, object]] = []

    def _select_directory(*_args: object, **kwargs: object) -> Path:
        select_calls.append(kwargs)
        return selected_root

    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.dialog_controller.dialogs.select_directory",
        _select_directory,
    )

    assert controller.bind_library_dialog() == selected_root
    assert select_calls == [{"use_qt_directory_dialog_on_macos": True}]

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
    )
    status_bar.showMessage.assert_called_once_with(
        f"Basic Library bound to {selected_root}"
    )


def test_bind_library_dialog_skips_initial_scan_when_scope_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    scan_service = Mock()
    scan_service.is_scan_scope_complete.return_value = True
    context = _Context(None, scan_service=scan_service)
    status_bar = Mock()
    controller = DialogController(object(), context, status_bar)

    monkeypatch.setattr(
        "iPhoto.gui.ui.controllers.dialog_controller.dialogs.select_directory",
        lambda *_args, **_kwargs: selected_root,
    )

    assert controller.bind_library_dialog() == selected_root

    scan_service.is_scan_scope_complete.assert_called_once_with(selected_root)
    context.facade.scan_root_async.assert_not_called()
