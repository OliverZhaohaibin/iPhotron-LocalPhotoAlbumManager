from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtTest")

from PySide6.QtTest import QSignalSpy

from iPhoto.gui.coordinators.view_router import ViewRouter


class FakeStack:
    def __init__(self, widgets):
        self._widgets = list(widgets)
        self._current_index = 0

    def indexOf(self, widget):
        try:
            return self._widgets.index(widget)
        except ValueError:
            return -1

    def setCurrentIndex(self, index):
        self._current_index = index

    def currentIndex(self):
        return self._current_index

    def currentWidget(self):
        if 0 <= self._current_index < len(self._widgets):
            return self._widgets[self._current_index]
        return None


def test_view_router_emits_when_switching_views(qtbot):
    gallery_page = object()
    people_page = object()
    map_page = object()
    detail_page = object()
    dashboard_page = object()

    stack = FakeStack([gallery_page, people_page, map_page, detail_page, dashboard_page])
    ui = SimpleNamespace(
        view_stack=stack,
        gallery_page=gallery_page,
        people_page=people_page,
        detail_page=detail_page,
        map_page=map_page,
        albums_dashboard_page=dashboard_page,
    )

    router = ViewRouter(ui)
    spy = QSignalSpy(router.detailViewShown)

    stack.setCurrentIndex(0)
    router.show_detail()

    assert stack.currentIndex() == stack.indexOf(detail_page)
    assert spy.count() == 1


def test_view_router_show_people_switches_to_people_page(qtbot):
    gallery_page = object()
    people_page = object()
    detail_page = object()

    stack = FakeStack([gallery_page, people_page, detail_page])
    ui = SimpleNamespace(
        view_stack=stack,
        gallery_page=gallery_page,
        people_page=people_page,
        detail_page=detail_page,
    )

    router = ViewRouter(ui)
    spy = QSignalSpy(router.peopleViewShown)

    stack.setCurrentIndex(0)
    router.show_people()

    assert stack.currentIndex() == stack.indexOf(people_page)
    assert spy.count() == 1


def test_view_router_no_emit_when_view_unchanged(qtbot):
    gallery_page = object()
    detail_page = object()

    stack = FakeStack([gallery_page, detail_page])
    ui = SimpleNamespace(
        view_stack=stack,
        gallery_page=gallery_page,
        detail_page=detail_page,
    )

    router = ViewRouter(ui)
    spy = QSignalSpy(router.galleryViewShown)

    stack.setCurrentIndex(stack.indexOf(gallery_page))
    router.show_gallery()

    assert stack.currentIndex() == stack.indexOf(gallery_page)
    assert spy.count() == 0


@pytest.mark.parametrize(
    ("feature", "method_name", "signal_name"),
    [
        ("map", "show_map", "mapViewShown"),
        ("albums", "show_albums_dashboard", "dashboardViewShown"),
    ],
)
def test_view_router_creates_optional_page_on_first_visit(
    qtbot, feature, method_name, signal_name
):
    gallery_page = object()
    detail_page = object()
    optional_page = object()
    stack = FakeStack([gallery_page, detail_page])
    calls = []

    class LazyUi:
        def __init__(self):
            self.view_stack = stack
            self.gallery_page = gallery_page
            self.detail_page = detail_page

        def ensure_feature(self, requested):
            calls.append(requested)
            if optional_page not in stack._widgets:
                stack._widgets.append(optional_page)
            return optional_page

    router = ViewRouter(LazyUi())
    spy = QSignalSpy(getattr(router, signal_name))

    getattr(router, method_name)()
    getattr(router, method_name)()

    assert calls == [feature]
    assert stack.currentWidget() is optional_page
    assert spy.count() == 1
