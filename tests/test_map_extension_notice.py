import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from iPhoto.gui.ui.widgets import map_extension_notice as notice_module
from maps import map_sources


@pytest.mark.parametrize("url", [None, "https://example.org/map-update.zip"])
def test_update_notice_only_for_failure_and_uses_reserved_link(monkeypatch, url):
    app = QApplication.instance() or QApplication([])
    app.setProperty("nativeMapUpdateNoticeShown", False)
    monkeypatch.setattr(notice_module, "map_extension_update_url", lambda: url)
    opened = []
    monkeypatch.setattr(
        notice_module.QDesktopServices, "openUrl", lambda link: opened.append(link.toString())
    )
    parent = QWidget()
    try:
        notice_module.show_map_extension_update_notice(parent, "load failure")
        assert parent.findChildren(QMessageBox) == []
        parent.show()
        notice_module.show_map_extension_update_notice(parent, None)
        assert parent.findChildren(QMessageBox) == []
        notice_module.show_map_extension_update_notice(parent, "load failure")
        notice_module.show_map_extension_update_notice(parent, "load failure")
        notices = parent.findChildren(QMessageBox)
        assert len(notices) == 1
        assert not notices[0].isModal()
        buttons = [
            button
            for button in notices[0].buttons()
            if notices[0].buttonRole(button) == QMessageBox.ButtonRole.ActionRole
        ]
        assert len(buttons) == int(url is not None)
        if buttons:
            buttons[0].click()
            assert opened == [url]
        else:
            assert opened == []
    finally:
        for notice in parent.findChildren(QMessageBox):
            notice.close()
        parent.close()
        app.setProperty("nativeMapUpdateNoticeShown", False)


def test_update_urls_are_empty_and_platform_specific(monkeypatch):
    assert all(url is None for url in map_sources.MAP_EXTENSION_UPDATE_URLS.values())
    monkeypatch.setitem(
        map_sources.MAP_EXTENSION_UPDATE_URLS, "linux", "https://example.org/linux.tar.xz"
    )
    assert map_sources.map_extension_update_url("linux2") == "https://example.org/linux.tar.xz"
    assert map_sources.map_extension_update_url("darwin") is None
