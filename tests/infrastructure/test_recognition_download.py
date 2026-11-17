from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from iPhoto.application.ports.extension_install import ExtensionInstallError
from iPhoto.infrastructure.services import recognition_download as module


class _Reply(QObject):
    readyRead = Signal()
    finished = Signal()

    def __init__(self, content, *, complete):
        super().__init__()
        self.content = content
        self.complete = complete
        self.aborted = False

    def setReadBufferSize(self, _size):
        pass

    def isFinished(self):
        return self.complete

    def bytesAvailable(self):
        return len(self.content)

    def read(self, size):
        chunk, self.content = self.content[:size], self.content[size:]
        return chunk

    def header(self, _header):
        return None

    def attribute(self, _attribute):
        return 200

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def abort(self):
        self.aborted = True
        self.finished.emit()


@pytest.fixture
def mock_network(monkeypatch):
    def configure(reply):
        def get(request):
            assert request.url() == QUrl("https://example.test/archive.zip")
            assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == (
                QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy
            )
            return reply

        manager = SimpleNamespace(
            get=get, setProxyFactory=lambda _factory: None, clearConnectionCache=lambda: None
        )
        monkeypatch.setattr(module, "QNetworkAccessManager", lambda: manager)

    return configure


def test_cancellation_aborts_reply_even_before_data_arrives(qapp, mock_network, tmp_path):
    reply = _Reply(b"", complete=False)
    mock_network(reply)
    cancelled = False

    def cancel():
        nonlocal cancelled
        cancelled = True

    def check_cancelled():
        if cancelled:
            raise CancelledError()

    package = SimpleNamespace(url="https://example.test/archive.zip", size=3)
    guard = QTimer(singleShot=True)
    guard.timeout.connect(reply.finished)
    guard.start(1000)
    QTimer.singleShot(0, cancel)
    try:
        with pytest.raises(CancelledError):
            module.download_archive(
                package, tmp_path / "archive.part", lambda *_: None, "Downloading…", "test",
                check_cancelled=check_cancelled,
            )
    finally:
        guard.stop()

    assert reply.aborted


@pytest.mark.parametrize("content", [b"zip", b"oversized"])
def test_stream_checks_size_without_content_length(qapp, mock_network, tmp_path, content):
    mock_network(_Reply(content, complete=True))
    package = SimpleNamespace(url="https://example.test/archive.zip", size=3)
    partial = tmp_path / "archive.part"

    def download():
        module.download_archive(
            package, partial, lambda *_: None, "Downloading…", "test",
            check_cancelled=lambda: None,
        )

    if len(content) == package.size:
        download()
        assert partial.read_bytes() == content
    else:
        with pytest.raises(ExtensionInstallError, match="category=integrity"):
            download()
