"""Cancellable HTTPS transfer for the recognition installer."""

from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkProxyFactory,
    QNetworkReply,
    QNetworkRequest,
)

from iPhoto.application.ports.extension_install import ExtensionInstallError, ExtensionProgress

from .extension_archives import ReleaseArchive, install_failure

_CHUNK_SIZE = 256 * 1024


class _ProxyFactory(QNetworkProxyFactory):
    def queryProxy(self, query):
        host = query.url().host() or query.peerHostName()
        if host and proxy_bypass(host):
            return [QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)]
        proxy = getproxies().get("https")
        if proxy:
            try:
                address = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
                port = address.port or 80
            except ValueError:
                return self.systemProxyForQuery(query)
            if address.hostname and address.scheme == "http":
                return [QNetworkProxy(
                    QNetworkProxy.ProxyType.HttpProxy,
                    address.hostname,
                    port,
                    unquote(address.username or ""),
                    unquote(address.password or ""),
                )]
        return self.systemProxyForQuery(query)


def download_archive(
    package: ReleaseArchive,
    partial: Path,
    progress: ExtensionProgress,
    message: str,
    user_agent: str,
    *,
    check_cancelled: Callable[[], None],
) -> None:
    """Stream a bounded archive and abort the reply when the caller cancels."""
    check_cancelled()
    url = QUrl(package.url)
    if url.scheme() != "https":
        raise ExtensionInstallError("network", "download", detail="HTTPS is required")

    network = QNetworkAccessManager()
    # Keep the Python wrapper alive while Qt calls its virtual queryProxy().
    proxy_factory = _ProxyFactory()
    network.setProxyFactory(proxy_factory)
    request = QNetworkRequest(url)
    request.setRawHeader(b"User-Agent", user_agent.encode("ascii"))
    request.setRawHeader(b"Accept-Encoding", b"identity")
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    request.setTransferTimeout(30_000)
    loop = QEventLoop()
    cancellation_timer = QTimer()
    cancellation_timer.setInterval(50)
    progress(0, 0, message)

    try:
        with partial.open("wb") as handle:
            reply = network.get(request)
            reply.setReadBufferSize(_CHUNK_SIZE)
            received = 0
            failure = None

            def consume() -> None:
                nonlocal received
                check_cancelled()
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status and status >= 400:
                    raise ExtensionInstallError("http", "download", code=status)
                length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
                if length is not None and int(length) != package.size:
                    raise ExtensionInstallError("integrity", "download")
                while reply.bytesAvailable():
                    check_cancelled()
                    chunk = reply.read(_CHUNK_SIZE)
                    received += len(chunk)
                    if received > package.size:
                        raise ExtensionInstallError("integrity", "download")
                    handle.write(chunk)
                    progress(received, package.size, message)

            def invoke(callback: Callable[[], None]) -> None:
                nonlocal failure
                if failure is not None:
                    return
                try:
                    callback()
                except Exception as exc:
                    failure = exc
                    reply.abort()
                    loop.quit()

            reply.readyRead.connect(lambda: invoke(consume))
            reply.finished.connect(loop.quit)
            cancellation_timer.timeout.connect(lambda: invoke(check_cancelled))
            cancellation_timer.start()
            try:
                if not reply.isFinished():
                    loop.exec()
            finally:
                cancellation_timer.stop()
            if failure is not None:
                raise failure
            check_cancelled()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status and status >= 400:
                raise ExtensionInstallError("http", "download", code=status)
            error = reply.error()
            if error != QNetworkReply.NetworkError.NoError:
                categories = {
                    QNetworkReply.NetworkError.HostNotFoundError: "dns",
                    QNetworkReply.NetworkError.ConnectionRefusedError: "refused",
                    QNetworkReply.NetworkError.SslHandshakeFailedError: "tls",
                    QNetworkReply.NetworkError.TimeoutError: "timeout",
                    QNetworkReply.NetworkError.OperationCanceledError: "timeout",
                    QNetworkReply.NetworkError.RemoteHostClosedError: "interrupted",
                }
                raise ExtensionInstallError(
                    categories.get(error, "network"), "download",
                    code=error.value, detail=f"host={reply.url().host()}",
                )
            consume()
            if received != package.size:
                raise ExtensionInstallError("interrupted", "download")
    except OSError as exc:
        raise install_failure(exc, "download", f"host={url.host()}") from exc
    finally:
        network.clearConnectionCache()
