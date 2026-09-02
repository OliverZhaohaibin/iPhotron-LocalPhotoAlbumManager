"""A non-modal update notice for an actual native-map fallback."""

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from maps.map_sources import map_extension_update_url


def show_map_extension_update_notice(parent: QWidget, reason: str | None) -> None:
    app = QApplication.instance()
    if not reason or app is None or not parent.isVisible():
        return
    if app.property("nativeMapUpdateNoticeShown"):
        return
    app.setProperty("nativeMapUpdateNoticeShown", True)

    def tr(text: str) -> str:
        return QCoreApplication.translate("MapExtension", text)

    notice = QMessageBox(parent.window())
    notice.setIcon(QMessageBox.Icon.Information)
    notice.setWindowTitle(tr("Map Extension"))
    notice.setText(
        tr(
            "The map extension could not be used. Using the basic map. Please update the map extension."
        )
    )
    url = map_extension_update_url()
    if url:
        download = notice.addButton(tr("Download Update"), QMessageBox.ButtonRole.ActionRole)
        download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    else:
        notice.setInformativeText(tr("An update download will be available soon."))
    notice.addButton(QMessageBox.StandardButton.Ok)
    notice.setModal(False)
    notice.finished.connect(notice.deleteLater)
    notice.show()
