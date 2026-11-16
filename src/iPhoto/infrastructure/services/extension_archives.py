"""Filesystem and network primitives used by verified extension installers."""

from __future__ import annotations

import errno
import hashlib
import http.client
import os
import shutil
import socket
import ssl
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, TypeVar
from urllib import error, parse, request

from iPhoto.application.ports.extension_install import ExtensionInstallError, ExtensionProgress


class ReleaseArchive(Protocol):
    @property
    def filename(self) -> str: ...
    @property
    def size(self) -> int: ...
    @property
    def sha256(self) -> str: ...
    @property
    def url(self) -> str: ...


_Package = TypeVar("_Package", bound=ReleaseArchive)

_CHUNK = 256 * 1024
_TIMEOUT = 30


@contextmanager
def install_lock(path: Path):
    # OS locks are released on process exit. An age-based lock could expire in
    # the middle of a slow 500 MB download and permit concurrent activation.
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ExtensionInstallError("busy", "prepare") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def file_digest(path: Path, *, check_cancelled: Callable[[], None] | None = None) -> str:
    with path.open("rb") as handle:
        if check_cancelled is None:
            return hashlib.file_digest(handle, "sha256").hexdigest()
        digest = hashlib.sha256()
        check_cancelled()
        while chunk := handle.read(_CHUNK):
            check_cancelled()
            digest.update(chunk)
        return digest.hexdigest()


class _DownloadHTTPSHandler(request.HTTPSHandler):
    """Record the hostname of the current download hop for diagnostics."""

    def __init__(self, context: ssl.SSLContext):
        super().__init__(context=context)
        self.host = ""

    def https_open(self, req):
        self.host = parse.urlsplit(req.full_url).hostname or "unknown"
        return super().https_open(req)


class _DownloadRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if parse.urlsplit(newurl).scheme.lower() != "https":
            raise error.HTTPError(req.full_url, code, msg, headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # The proxy handler will add credentials for the next hop if needed.
            redirected.remove_header("Proxy-authorization")
        return redirected


def install_failure(exc: Exception, stage: str, detail: str = "") -> ExtensionInstallError:
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
    if isinstance(exc, error.HTTPError):
        category, code = "http", exc.code
    elif isinstance(reason, tarfile.FilterError):
        category = "unsafe_archive"
    elif isinstance(reason, (zipfile.BadZipFile, tarfile.ReadError)):
        category = "integrity"
    elif isinstance(reason, ssl.SSLError):
        category = "tls"
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        category = "timeout"
    elif isinstance(reason, socket.gaierror):
        category = "dns"
    elif isinstance(reason, ConnectionRefusedError) or code == 10061:
        category = "refused"
    elif isinstance(reason, PermissionError) or code in {5, 13, 32, 33}:
        category = "permission"
    elif code in {errno.ENOSPC, 112}:
        category = "disk"
    elif isinstance(reason, (ConnectionError, http.client.IncompleteRead)):
        category = "interrupted"
    else:
        category = "network" if stage == "download" else "installation"
    return ExtensionInstallError(category, stage, code=code, detail=detail)


def _download_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    if paths.cafile is None and paths.capath is None and context.cert_store_stats()["x509_ca"] == 0:
        # Some Python distributions ship without a configured certificate store.
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    return context


def download_archive(
    package: ReleaseArchive,
    partial: Path,
    progress: ExtensionProgress,
    message: str,
    user_agent: str,
) -> None:
    host = parse.urlsplit(package.url).hostname
    route = None
    try:
        route = _DownloadHTTPSHandler(_download_tls_context())
        opener = request.build_opener(route, _DownloadRedirectHandler())
        progress(0, 0, message)
        req = request.Request(
            package.url, headers={"User-Agent": user_agent}
        )
        with opener.open(req, timeout=_TIMEOUT) as response, partial.open("wb") as handle:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != package.size:
                raise ExtensionInstallError("integrity", "download")
            received = 0
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > package.size:
                    raise ExtensionInstallError("integrity", "download")
                handle.write(chunk)
                progress(received, package.size, message)
            if received != package.size:
                raise http.client.IncompleteRead(b"", package.size - received)
    except ExtensionInstallError:
        raise
    except Exception as exc:
        failed_host = (route.host if route is not None else None) or host
        raise install_failure(exc, "download", f"host={failed_host}") from exc



def match_archive(
    archive: Path,
    packages: Iterable[_Package],
    *,
    stage: str = "verify",
    check_cancelled: Callable[[], None] | None = None,
) -> _Package:
    if not archive.is_file():
        raise ExtensionInstallError("missing_file", stage)
    size = archive.stat().st_size
    candidates = [package for package in packages if package.size == size]
    if not candidates:
        raise ExtensionInstallError("unknown_package", stage)
    digest = file_digest(archive, check_cancelled=check_cancelled)
    for package in candidates:
        if package.sha256 == digest:
            return package
    raise ExtensionInstallError("integrity", stage)


@contextmanager
def copied_archive(
    source: Path, target_dir: Path, *, prefix: str, check_cancelled: Callable[[], None] | None = None
):
    if not source.is_file():
        raise ExtensionInstallError("missing_file", "verify")
    with tempfile.TemporaryDirectory(
        prefix=prefix, dir=target_dir, ignore_cleanup_errors=True
    ) as directory:
        archive = Path(directory) / "archive"
        if check_cancelled is None:
            shutil.copyfile(source, archive)
        else:
            with source.open("rb") as input_file, archive.open("wb") as output_file:
                _copy_chunks(input_file, output_file, check_cancelled)
        yield archive


def cached_download(
    target_dir: Path,
    package: _Package,
    download: Callable[[Path], None],
    verify: Callable[[Path], None],
) -> Path:
    cache = target_dir / ".downloads"
    cache.mkdir(exist_ok=True)
    archive = cache / f"{package.sha256}-{package.filename}"
    if archive.is_file():
        try:
            verify(archive)
            return archive
        except ExtensionInstallError:
            archive.unlink()
    partial = archive.with_name(archive.name + ".part")
    try:
        download(partial)
        verify(partial)
        partial.replace(archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive


def probe_target(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Exercise both write and rename to detect read-only directories and ACLs.
    with tempfile.TemporaryDirectory(prefix=".probe-", dir=directory) as probe:
        source = Path(probe) / "write"
        source.write_bytes(b"probe")
        source.replace(Path(probe) / "renamed")


def check_member_path(name: str, *, root: str | None = None) -> None:
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.drive
        or windows.root
        or "\\" in name
        or ".." in posix.parts
        or not posix.parts
        or (root is not None and posix.parts[0] != root)
        or any(
            ":" in part or part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()
            for part in posix.parts
        )
    ):
        raise ExtensionInstallError("unsafe_archive", "extract")


def _copy_chunks(source, destination, check_cancelled: Callable[[], None]) -> None:
    check_cancelled()
    while chunk := source.read(_CHUNK):
        check_cancelled()
        destination.write(chunk)


def extract_zip(
    archive: Path,
    directory: Path,
    *,
    root: str | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            check_member_path(member.filename, root=root)
            if stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ExtensionInstallError("unsafe_archive", "extract")
        if check_cancelled is None:
            zipped.extractall(directory)
            return
        for member in zipped.infolist():
            check_cancelled()
            destination = directory / PurePosixPath(member.filename)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(member) as source, destination.open("wb") as output:
                _copy_chunks(source, output, check_cancelled)
