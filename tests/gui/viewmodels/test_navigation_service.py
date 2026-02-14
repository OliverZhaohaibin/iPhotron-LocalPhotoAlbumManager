"""Tests for NavigationService — pure Python, no Qt dependency."""

from iPhoto.gui.services.navigation_service import NavigationService


class TestNavigationService:
    def test_navigate_to(self):
        nav = NavigationService()
        nav.navigate_to("album_view", album_id="a1")

        assert nav.current_page == "album_view"
        assert nav.current_params == {"album_id": "a1"}

    def test_page_changed_signal(self):
        nav = NavigationService()
        events = []
        nav.page_changed.connect(lambda page, params: events.append((page, params)))

        nav.navigate_to("detail", asset_id="x")

        assert events == [("detail", {"asset_id": "x"})]

    def test_go_back(self):
        nav = NavigationService()
        nav.navigate_to("page1")
        nav.navigate_to("page2")

        result = nav.go_back()

        assert result is True
        assert nav.current_page == "page1"

    def test_go_back_at_root(self):
        nav = NavigationService()
        nav.navigate_to("page1")

        result = nav.go_back()

        assert result is False
        assert nav.current_page == "page1"

    def test_go_back_empty_history(self):
        nav = NavigationService()
        result = nav.go_back()
        assert result is False

    def test_can_go_back(self):
        nav = NavigationService()
        assert nav.can_go_back is False

        nav.navigate_to("page1")
        assert nav.can_go_back is False

        nav.navigate_to("page2")
        assert nav.can_go_back is True

    def test_history_depth(self):
        nav = NavigationService()
        assert nav.history_depth == 0

        nav.navigate_to("a")
        nav.navigate_to("b")
        nav.navigate_to("c")

        assert nav.history_depth == 3

    def test_clear_history(self):
        nav = NavigationService()
        nav.navigate_to("a")
        nav.navigate_to("b")
        nav.clear_history()

        assert nav.history_depth == 0
        assert nav.current_page is None

    def test_go_back_emits_signal(self):
        nav = NavigationService()
        events = []
        nav.page_changed.connect(lambda page, params: events.append(page))

        nav.navigate_to("page1")
        nav.navigate_to("page2")
        nav.go_back()

        assert events == ["page1", "page2", "page1"]

    def test_current_params_empty(self):
        nav = NavigationService()
        assert nav.current_params == {}
