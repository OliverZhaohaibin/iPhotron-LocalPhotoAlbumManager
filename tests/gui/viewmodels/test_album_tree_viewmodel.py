"""Tests for AlbumTreeViewModel — pure Python, no Qt dependency."""

from unittest.mock import Mock, MagicMock
from pathlib import Path

from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanCompletedEvent
from iPhoto.gui.viewmodels.album_tree_viewmodel import AlbumTreeViewModel


def _make_vm():
    album_service = Mock()
    bus = EventBus()
    vm = AlbumTreeViewModel(album_service=album_service, event_bus=bus)
    return vm, album_service, bus


class TestAlbumTreeViewModel:
    def test_open_album_sets_state(self):
        vm, svc, _ = _make_vm()
        response = Mock(album_id="album-1")
        svc.open_album.return_value = response

        vm.open_album(Path("/photos/album1"))

        assert vm.current_album_id.value == "album-1"
        assert vm.current_album_path.value == "/photos/album1"
        assert vm.loading.value is False

    def test_open_album_emits_signal(self):
        vm, svc, _ = _make_vm()
        response = Mock(album_id="a1")
        svc.open_album.return_value = response
        received = []
        vm.album_opened.connect(lambda r: received.append(r))

        vm.open_album(Path("/p"))

        assert len(received) == 1

    def test_open_album_error_handling(self):
        vm, svc, _ = _make_vm()
        svc.open_album.side_effect = RuntimeError("fail")
        errors = []
        vm.error_occurred.connect(lambda msg: errors.append(msg))

        vm.open_album(Path("/bad"))

        assert len(errors) == 1
        assert "fail" in errors[0]
        assert vm.loading.value is False

    def test_scan_current_album(self):
        vm, svc, _ = _make_vm()
        svc.open_album.return_value = Mock(album_id="a1")
        vm.open_album(Path("/p"))
        finished = []
        vm.scan_finished.connect(lambda: finished.append(True))

        vm.scan_current_album()

        svc.scan_album.assert_called_once_with("a1")
        assert len(finished) == 1

    def test_scan_no_album_does_nothing(self):
        vm, svc, _ = _make_vm()
        vm.scan_current_album()
        svc.scan_album.assert_not_called()

    def test_select_album(self):
        vm, _, _ = _make_vm()
        vm.select_album("album-2")
        assert vm.current_album_id.value == "album-2"

    def test_scan_completed_event_updates_state(self):
        vm, svc, bus = _make_vm()
        svc.open_album.return_value = Mock(album_id="a1")
        vm.open_album(Path("/p"))

        bus.publish(ScanCompletedEvent(album_id="a1", asset_count=10))

        assert vm.scan_progress.value == 1.0
        assert vm.loading.value is False

    def test_scan_completed_other_album_ignored(self):
        vm, svc, bus = _make_vm()
        svc.open_album.return_value = Mock(album_id="a1")
        vm.open_album(Path("/p"))
        vm.scan_progress.value = 0.5

        bus.publish(ScanCompletedEvent(album_id="other", asset_count=5))

        assert vm.scan_progress.value == 0.5  # unchanged

    def test_dispose(self):
        vm, svc, bus = _make_vm()
        svc.open_album.return_value = Mock(album_id="a1")
        vm.open_album(Path("/p"))

        vm.dispose()

        bus.publish(ScanCompletedEvent(album_id="a1", asset_count=99))
        # Should not change because disposed
        assert vm.scan_progress.value == 0.0
