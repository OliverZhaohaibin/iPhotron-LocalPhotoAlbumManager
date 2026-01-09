from iPhoto.gui.facade import AppFacade


class _DummyLibrary:
    def __init__(self) -> None:
        self.cancelled = False

    def stop_scanning(self) -> None:
        self.cancelled = True


class _DummyUpdateService:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel_active_scan(self) -> None:
        self.cancelled = True


def test_cancel_active_scans_requests_cancellation() -> None:
    facade = AppFacade()
    fake_library = _DummyLibrary()
    fake_update = _DummyUpdateService()

    facade._inject_scan_dependencies_for_tests(
        library_manager=fake_library, library_update_service=fake_update
    )

    facade.cancel_active_scans()

    assert fake_library.cancelled is True
    assert fake_update.cancelled is True
