from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.services import location_search_controller as controller_module
from iPhoto.gui.services.location_search_controller import LocationSearchController
from maps.osmand_search import SearchSuggestion


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _wait_until(qapp: QApplication, predicate, *, timeout_ms: int = 1000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


class _FakeSearchService:
    created: list["_FakeSearchService"] = []
    calls: list[str] = []

    def __init__(self, *, package_root: Path | None = None) -> None:
        self.package_root = Path(package_root) if package_root is not None else None
        self.shutdown_called = False
        self.created.append(self)

    def search(self, query: str, **kwargs) -> list[SearchSuggestion]:
        assert kwargs.get("fallback_on_empty") is False
        self.calls.append(query)
        display_name = "Munich" if query == "mun" else query.title()
        return [
            SearchSuggestion(
                display_name=display_name,
                secondary_text="Test",
                longitude=1.0,
                latitude=2.0,
                source_kind="fake",
                match_kind="exact",
            )
        ]

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.fixture(autouse=True)
def reset_fake_search_service() -> None:
    _FakeSearchService.created.clear()
    _FakeSearchService.calls.clear()


def test_location_search_controller_discards_stale_token_results(
    qapp: QApplication,
) -> None:
    del qapp
    controller = LocationSearchController()
    target_path = Path("/fake/photo.jpg")
    emitted: list[tuple[int, object, str, object]] = []
    controller.suggestionsReady.connect(lambda *args: emitted.append(args))
    controller._token = 2
    controller._target_path = target_path

    controller._handle_ready(
        1,
        target_path,
        "old",
        [SimpleNamespace(display_name="Old", secondary_text="")],
    )
    controller._handle_ready(
        2,
        target_path,
        "new",
        [SimpleNamespace(display_name="New", secondary_text="")],
    )

    assert len(emitted) == 1
    assert emitted[0][0] == 2
    assert emitted[0][2] == "new"
    controller.shutdown()


def test_location_search_controller_debounces_rapid_typing_and_reuses_service(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller_module, "OsmAndSearchService", _FakeSearchService)
    controller = LocationSearchController()
    target_path = Path("/fake/photo.jpg")
    emitted: list[tuple[int, object, str, object]] = []
    controller.suggestionsReady.connect(lambda *args: emitted.append(args))

    try:
        controller.search(
            "mu",
            target_path=target_path,
            package_root=Path("/fake/maps"),
            locale="en",
        )
        controller.search(
            "mun",
            target_path=target_path,
            package_root=Path("/fake/maps"),
            locale="en",
        )
        controller.search(
            "muni",
            target_path=target_path,
            package_root=Path("/fake/maps"),
            locale="en",
        )

        _wait_until(qapp, lambda: _FakeSearchService.calls == ["muni"])
        _wait_until(
            qapp,
            lambda: any(
                query == "muni" and suggestions and suggestions[0].display_name == "Muni"
                for _token, _target, query, suggestions in emitted
            ),
        )
    finally:
        controller.shutdown()

    assert len(_FakeSearchService.created) == 1
    assert _FakeSearchService.created[0].shutdown_called is True


def test_location_search_controller_reset_preserves_warm_service(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller_module, "OsmAndSearchService", _FakeSearchService)
    controller = LocationSearchController()

    try:
        controller.warm_up(package_root=Path("/fake/maps"), locale="en")
        _wait_until(qapp, lambda: len(_FakeSearchService.created) == 1)

        controller.reset()
        controller.search(
            "mu",
            target_path=Path("/fake/photo.jpg"),
            package_root=Path("/fake/maps"),
            locale="en",
        )
        _wait_until(qapp, lambda: _FakeSearchService.calls == ["mu"])
    finally:
        controller.shutdown()

    assert len(_FakeSearchService.created) == 1
    assert _FakeSearchService.created[0].shutdown_called is True


def test_location_search_controller_emits_cached_preview_before_refresh(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller_module, "OsmAndSearchService", _FakeSearchService)
    controller = LocationSearchController()
    target_path = Path("/fake/photo.jpg")
    emitted: list[tuple[int, object, str, object]] = []
    controller.suggestionsReady.connect(lambda *args: emitted.append(args))

    try:
        controller.search(
            "mun",
            target_path=target_path,
            package_root=Path("/fake/maps"),
            locale="en",
        )
        _wait_until(qapp, lambda: _FakeSearchService.calls == ["mun"])

        preview_token = controller.search(
            "muni",
            target_path=target_path,
            package_root=Path("/fake/maps"),
            locale="en",
        )
        preview_emission = emitted[-1]

        assert preview_emission[0] == preview_token
        assert preview_emission[2] == "muni"
        assert preview_emission[3][0].display_name == "Munich"
        _wait_until(qapp, lambda: _FakeSearchService.calls == ["mun", "muni"])
    finally:
        controller.shutdown()
