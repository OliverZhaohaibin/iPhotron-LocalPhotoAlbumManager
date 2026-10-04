"""Network/filesystem adapter for verified, recoverable map installations."""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import logging
import os
import shutil
import socket
import ssl
import stat
import struct
import tarfile
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib import error, parse, request

from iPhoto.application.ports.map_extension import (
    MapExtensionError,
    MapExtensionProgress,
    MapExtensionRequest,
    MapExtensionResult,
)
from maps import map_sources

from .map_extension_packages import PACKAGES, MapExtensionPackage

_LOG = logging.getLogger(__name__)
_CHUNK = 256 * 1024
_TIMEOUT = 30


@contextmanager
def _install_lock(path: Path):
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
            raise MapExtensionError("busy", "prepare") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _endpoint(value: str) -> str:
    """Never include proxy passwords, URL queries or signed redirect tokens."""
    try:
        parsed = parse.urlsplit(value if "://" in value else "http://" + value)
        return f"{parsed.hostname or 'unknown'}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    except ValueError:
        return "invalid"


def _failure(exc: Exception, stage: str, detail: str = "") -> MapExtensionError:
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
    return MapExtensionError(category, stage, code=code, detail=detail)


class MapExtensionInstaller:
    def __init__(self, packages: tuple[MapExtensionPackage, ...] = PACKAGES):
        self._packages = packages

    def _supported_packages(self, platform: str) -> tuple[MapExtensionPackage, ...]:
        return tuple(p for p in self._packages if p.platform == platform and p.app_major == 6)

    def supports_local_install(self, platform: str) -> bool:
        return bool(self._supported_packages(platform))

    def download_url(self, platform: str) -> str | None:
        package = next(iter(self._supported_packages(platform)), None)
        return package.url if package else None

    def execute(
        self, payload: MapExtensionRequest, progress: MapExtensionProgress
    ) -> MapExtensionResult:
        target = map_sources.managed_osmand_extension_root(payload.package_root)
        stage = "prepare"
        manifest = Path(payload.package_root).parent / "build-manifest.json"
        identity = {}
        if manifest.is_file():
            try:
                candidate = json.loads(manifest.read_text(encoding="utf-8"))
                identity = candidate if isinstance(candidate, dict) else {}
            except (OSError, ValueError):
                pass
        _LOG.info(
            "Map component operation=%s platform=%s root=%s app=%s revision=%s",
            payload.operation,
            payload.platform,
            target,
            identity.get("app_version", "source"),
            identity.get("source_revision", "unknown"),
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # A real write/rename probe also catches ACLs that os.access misses.
            with tempfile.TemporaryDirectory(prefix=".probe-", dir=target.parent) as probe:
                source = Path(probe) / "write"
                source.write_bytes(b"probe")
                source.replace(Path(probe) / "renamed")
            with _install_lock(target.parent / ".map-install.lock"):
                if payload.operation == "prepare":
                    return self._prepare(payload, target, progress)
                stage = "download" if payload.local_archive_path is None else "verify"
                archive = self._obtain_archive(payload, target, progress)
                stage = "extract"
                result = self._stage_archive(payload, target, archive, progress)
                # Retain a verified download on failure or deferred activation.
                if result.status == "installed" and payload.local_archive_path is None:
                    self._cleanup(archive)
                return result
        except MapExtensionError as failure:
            _LOG.warning("Map component failure: %s", failure)
            raise
        except Exception as exc:
            failure = _failure(exc, stage, f"install_root={target}")
            _LOG.warning("Map component failure: %s", failure)
            raise failure from exc

    def _obtain_archive(self, payload, target, progress) -> Path:
        if payload.local_archive_path is not None:
            source = Path(payload.local_archive_path)
            self._verify_archive(source, payload.platform)
            return source
        package = next(iter(self._supported_packages(payload.platform)), None)
        if package is None:
            raise MapExtensionError("unsupported", "download")
        cache = target.parent / ".downloads"
        cache.mkdir(exist_ok=True)
        archive = cache / f"{package.sha256}-{package.filename}"
        if archive.is_file():
            try:
                self._verify_archive(archive, payload.platform)
                return archive
            except MapExtensionError:
                archive.unlink()
        partial = archive.with_name(archive.name + ".part")
        try:
            self._download(package, partial, payload.network_mode, progress)
            self._verify_archive(partial, payload.platform)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
        return archive

    def _download(self, package, partial, mode, progress):
        for attempt in range(3):
            proxies = {} if mode == "direct" else request.getproxies()
            source = (
                "direct"
                if mode == "direct"
                else ("environment" if request.getproxies_environment() else "system")
            )
            # A new opener on every attempt observes changed system proxies.
            opener = request.build_opener(request.ProxyHandler(proxies))
            host = parse.urlsplit(package.url).hostname or ""
            bypass = mode == "direct" or request.proxy_bypass(host)
            proxy = (
                "none"
                if bypass
                else _endpoint(proxies["https"])
                if proxies.get("https")
                else "none"
            )
            detail = f"host={host}; proxy_source={source}; proxy={proxy}; attempt={attempt + 1}"
            progress(0, 0, "Downloading map extension...")
            try:
                req = request.Request(
                    package.url, headers={"User-Agent": "iPhotron-map-installer/1"}
                )
                with opener.open(req, timeout=_TIMEOUT) as response, partial.open("wb") as handle:
                    response_url = getattr(response, "url", package.url)
                    detail += f"; response_host={parse.urlsplit(response_url).hostname or host}"
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != package.size:
                        raise MapExtensionError("integrity", "download", detail=detail)
                    received = 0
                    while chunk := response.read(_CHUNK):
                        received += len(chunk)
                        if received > package.size:
                            raise MapExtensionError("integrity", "download", detail=detail)
                        handle.write(chunk)
                        progress(received, package.size, "Downloading map extension...")
                    if received != package.size:
                        raise http.client.IncompleteRead(b"", package.size - received)
                return
            except MapExtensionError:
                raise
            except Exception as exc:
                failure = _failure(exc, "download", detail)
                transient = failure.category in {"timeout", "interrupted"} or (
                    failure.category == "http" and failure.code in {408, 429, 500, 502, 503, 504}
                )
                if not transient or attempt == 2:
                    raise failure from exc
                time.sleep((1, 3)[attempt])

    def _verify_archive(self, archive: Path, platform: str) -> None:
        if not archive.is_file():
            raise MapExtensionError("missing_file", "verify")
        size = archive.stat().st_size
        candidates = [p for p in self._packages if p.size == size]
        if not candidates:
            raise MapExtensionError("unknown_package", "verify")
        digest = _digest(archive)
        match = next((p for p in candidates if p.sha256 == digest), None)
        if match is None:
            raise MapExtensionError("integrity", "verify")
        if match not in self._supported_packages(platform):
            raise MapExtensionError("unsupported", "verify")

    def _validate(self, root: Path, platform: str) -> None:
        if root.is_symlink():
            raise MapExtensionError("unsafe_archive", "verify")
        resolved = root.resolve()
        for child in root.rglob("*"):
            if child.is_symlink() and not child.resolve().is_relative_to(resolved):
                raise MapExtensionError("unsafe_archive", "verify")
        if not map_sources.validate_osmand_extension_root(root, platform=platform):
            raise MapExtensionError("incomplete", "verify")
        if platform == "win32":
            for name in (
                "osmand_native_widget.dll",
                "OsmAndCore_shared.dll",
                "OsmAndCoreTools_shared.dll",
                "Qt6Core.dll",
            ):
                if not (root / "bin" / name).is_file():
                    raise MapExtensionError("incomplete", "verify", detail=f"missing=bin/{name}")
            # Check ABI without loading or executing untrusted native code.
            helper = next(p for p in (root / "bin").glob("osmand_render_helper*.exe"))
            with helper.open("rb") as handle:
                header = handle.read(64)
                if len(header) < 64 or header[:2] != b"MZ":
                    raise MapExtensionError("unsupported", "verify")
                handle.seek(struct.unpack_from("<I", header, 60)[0])
                pe = handle.read(6)
            expected = 0x8664 if struct.calcsize("P") == 8 else 0x14C
            if (
                len(pe) != 6
                or pe[:4] != b"PE\0\0"
                or struct.unpack_from("<H", pe, 4)[0] != expected
            ):
                raise MapExtensionError("unsupported", "verify")

    @staticmethod
    def _safe_name(name: str) -> None:
        posix, windows = PurePosixPath(name), PureWindowsPath(name)
        if (
            posix.is_absolute()
            or windows.drive
            or windows.root
            or "\\" in name
            or ".." in posix.parts
            or not posix.parts
            or posix.parts[0] != "extension"
            or any(":" in part or part.endswith((".", " ")) for part in posix.parts)
        ):
            raise MapExtensionError("unsafe_archive", "extract")

    def _extract(self, archive: Path, directory: Path):
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zipped:
                for member in zipped.infolist():
                    self._safe_name(member.filename)
                    if stat.S_ISLNK(member.external_attr >> 16):
                        raise MapExtensionError("unsafe_archive", "extract")
                zipped.extractall(directory)
        else:
            with tarfile.open(archive, "r:*") as tar:
                for member in tar.getmembers():
                    self._safe_name(member.name)
                    # Native macOS bundles may contain internal relative links;
                    # the data filter rejects links escaping the extraction root.
                tar.extractall(directory, filter="data")

    def _stage_archive(self, payload, target, archive, progress):
        staging = Path(tempfile.mkdtemp(prefix=".map-stage-", dir=target.parent))
        try:
            progress(0, 0, "Extracting map extension...")
            self._extract(archive, staging)
            progress(0, 0, "Validating map extension...")
            self._validate(staging / "extension", payload.platform)
            pending = target.with_name(target.name + ".pending")
            previous = target.with_name(target.name + ".pending.previous")
            if previous.exists():
                shutil.rmtree(previous)
            if pending.exists():
                pending.replace(previous)
            try:
                (staging / "extension").replace(pending)
            except Exception:
                if previous.exists():
                    previous.replace(pending)
                raise
            self._cleanup(previous)
            if payload.defer_activation:
                return MapExtensionResult(pending, target, "pending_restart")
            return self._activate(payload, target, progress)
        finally:
            self._cleanup(staging)

    def _activate(self, payload, target, progress):
        pending = target.with_name(target.name + ".pending")
        self._validate(pending, payload.platform)
        progress(0, 0, "Installing map extension...")
        try:
            map_sources.apply_pending_osmand_extension_install(
                payload.package_root, platform=payload.platform
            )
        except OSError as exc:
            # Windows may hold DLLs open even after the last widget is closed.
            if payload.platform == "win32" and (
                isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            ):
                return MapExtensionResult(pending, target, "pending_restart")
            raise _failure(exc, "activate", f"install_root={target}") from exc
        self._validate(target, payload.platform)
        return MapExtensionResult(pending, target)

    def _prepare(self, payload, target, progress):
        pending = target.with_name(target.name + ".pending")
        backup = target.with_name(target.name + ".backup")
        if not target.exists() and backup.exists():
            # Recover a process exit between the two directory renames.
            self._validate(backup, payload.platform)
            backup.replace(target)
        previous = target.with_name(target.name + ".pending.previous")
        if not pending.exists() and previous.exists():
            previous.replace(pending)
        legacy = Path(payload.package_root) / "tiles" / "extension.pending"
        if (
            not pending.exists()
            and not map_sources.validate_osmand_extension_root(target, platform=payload.platform)
            and legacy != pending
            and legacy.is_dir()
        ):
            self._validate(legacy, payload.platform)
            staging = Path(tempfile.mkdtemp(prefix=".map-recover-", dir=target.parent))
            try:
                shutil.copytree(legacy, staging / "extension")
                self._validate(staging / "extension", payload.platform)
                (staging / "extension").replace(pending)
            finally:
                self._cleanup(staging)
        if pending.exists():
            if payload.defer_activation:
                self._validate(pending, payload.platform)
                return MapExtensionResult(pending, target, "pending_restart")
            result = self._activate(payload, target, progress)
            if result.status == "installed":
                for package in self._packages:
                    self._cleanup(
                        target.parent / ".downloads" / f"{package.sha256}-{package.filename}"
                    )
            return result
        if map_sources.has_installed_osmand_extension(payload.package_root):
            return MapExtensionResult(
                pending, map_sources.default_osmand_extension_root(payload.package_root)
            )
        bundled = map_sources.bundled_osmand_extension_archive(payload.package_root)
        if bundled is not None:
            # This checksum is sealed alongside the tar inside the signed app.
            checksum = bundled.with_suffix(bundled.suffix + ".sha256")
            if not checksum.is_file() or checksum.read_text().strip() != _digest(bundled):
                raise MapExtensionError("integrity", "verify")
            return self._stage_archive(
                replace(payload, defer_activation=False), target, bundled, progress
            )
        return MapExtensionResult(pending, target, "missing")

    @staticmethod
    def _cleanup(path: Path):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            _LOG.warning("Map component cleanup deferred: %s", path)
