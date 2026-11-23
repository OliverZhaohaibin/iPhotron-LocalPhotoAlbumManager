from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QWidget

from iPhoto.bootstrap.library_probe import (
    LibraryProbeController,
    LibraryProbeRequest,
    ValidatedPreparedLibrary,
    probe_library,
)
from iPhoto.bootstrap.runtime_context import RuntimeContext
from iPhoto.cache.index_store.migrations import CURRENT_SCHEMA_VERSION, SchemaMigrator
from iPhoto.cache.index_store.repository import AssetRepository
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.gui.ui.controllers import dialog_controller
from iPhoto.gui.ui.controllers.dialog_controller import DialogController


@pytest.mark.parametrize("target", ["incomplete", "complete", "missing"])
def test_bind_library_dialog_uses_child_probe_before_switching(
    monkeypatch, tmp_path: Path, qapp, target: str,
) -> None:
    old_root = (tmp_path / "old").resolve()
    old_root.mkdir()
    selected_root = (tmp_path / "selected").resolve()
    if target != "missing":
        selected_root.mkdir()
        repository = AssetRepository(selected_root)
        if target == "complete":
            repository.create_scan_job(
                job_id="complete", root=selected_root.as_posix(),
                scope="library", status="running", stage="discover",
            )
            repository.update_scan_job_stage("complete", status="completed", finished=True)
        with closing(sqlite3.connect(repository.path)) as db:
            db.execute("PRAGMA user_version = 5")
        repository.close()

    window = QWidget()
    window.ui = SimpleNamespace(sidebar=Mock())
    settings = Mock()
    settings.get.side_effect = lambda _key, default=None: default
    context = RuntimeContext.create(defer_startup=True, settings=settings)
    old_session = context.commit_validated_library(ValidatedPreparedLibrary.create(
        probe_library(LibraryProbeRequest.create(old_root)),
    ))
    facade = context.facade
    monkeypatch.setattr(facade, "open_album", Mock())
    monkeypatch.setattr(facade, "scan_root_async", Mock())
    monkeypatch.setattr(facade, "cancel_active_scans", Mock(wraps=facade.cancel_active_scans))
    probe = LibraryProbeController(window)
    ready = QSignalSpy(probe.ready)
    failed = QSignalSpy(probe.failed)
    status_bar = Mock()
    controller = DialogController(window, context, status_bar)
    controller.set_library_probe_controller(probe)
    select_directory = Mock(return_value=selected_root)
    show_error = Mock()
    monkeypatch.setattr(dialog_controller.dialogs, "select_directory", select_directory)
    monkeypatch.setattr(dialog_controller.dialogs, "show_error", show_error)
    monkeypatch.setattr(
        SchemaMigrator, "prepare_database",
        Mock(side_effect=AssertionError("The GUI must use the prepared repository credential")),
    )
    heartbeats = []
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(lambda: heartbeats.append(True))
    heartbeat.start(5)

    try:
        assert controller.bind_library_dialog() == selected_root
        assert select_directory.call_args.kwargs == {"use_qt_directory_dialog_on_macos": True}
        assert controller.has_pending_library_switch()
        assert context.library_session is old_session
        settings.set.assert_not_called()
        facade.cancel_active_scans.assert_not_called()

        if target == "missing":
            assert failed.wait(5000)
            assert ready.count() == 0
            assert context.library_session is old_session
            settings.set.assert_not_called()
            facade.cancel_active_scans.assert_not_called()
            show_error.assert_called_once()
            return

        assert ready.wait(5000)
        assert heartbeats
        assert failed.count() == 0
        assert not controller.has_pending_library_switch()
        assert context.library_session.library_root == selected_root
        assert ready.at(0)[0].schema_version == CURRENT_SCHEMA_VERSION
        facade.cancel_active_scans.assert_called_once_with()
        facade.open_album.assert_called_once_with(selected_root)
        settings.set.assert_called_once_with("basic_library_path", str(selected_root))
        window.ui.sidebar.select_all_photos.assert_called_once_with(emit_signal=True)
        status_bar.showMessage.assert_called_once_with(f"Basic Library bound to {selected_root}")
        if target == "complete":
            facade.scan_root_async.assert_not_called()
        else:
            facade.scan_root_async.assert_called_once_with(
                selected_root, include=DEFAULT_INCLUDE, exclude=DEFAULT_EXCLUDE, startup=True,
            )
        SchemaMigrator.prepare_database.assert_not_called()
        show_error.assert_not_called()
    finally:
        heartbeat.stop()
        controller.cancel_library_switch()
        context.library.shutdown()
        context.close_library()


def test_dialog_rejects_superseded_and_cancelled_library_results(tmp_path: Path) -> None:
    context = Mock(library_epoch=1)
    context.request_library_switch_probe.side_effect = LibraryProbeRequest.create
    probe = Mock()
    controller = DialogController(object(), context, Mock())
    controller.set_library_probe_controller(probe)
    on_opened = Mock()

    controller.request_library_switch(tmp_path / "first", on_opened)
    first = probe.start.call_args.args[0]
    controller.request_library_switch(tmp_path / "second", on_opened)
    second = probe.start.call_args.args[0]
    controller._on_library_prepared(SimpleNamespace(request_id=first.request_id))
    assert controller.has_pending_library_switch()

    controller.cancel_library_switch()
    controller._on_library_prepared(SimpleNamespace(request_id=second.request_id))
    context.commit_validated_library.assert_not_called()
    on_opened.assert_not_called()
    probe.cancel.assert_called_once_with()


def test_bind_library_dialog_refuses_switch_during_active_edit(monkeypatch) -> None:
    context = Mock()
    select_directory = Mock()
    show_error = Mock()
    monkeypatch.setattr(dialog_controller.dialogs, "select_directory", select_directory)
    monkeypatch.setattr(dialog_controller.dialogs, "show_error", show_error)
    controller = DialogController(
        object(), context, Mock(), library_rebind_preflight=lambda: False,
    )

    assert controller.bind_library_dialog() is None
    select_directory.assert_not_called()
    context.request_library_switch_probe.assert_not_called()
    show_error.assert_called_once()
