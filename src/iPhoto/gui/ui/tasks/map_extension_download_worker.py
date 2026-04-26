"""Background worker that downloads and stages the map extension archive."""

from __future__ import annotations

import logging
import socket
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib import request

from PySide6.QtCore import QObject, QRunnable, Signal

from maps.map_sources import (
    apply_pending_osmand_extension_install,
    default_osmand_download_url,
    default_osmand_extension_root,
    default_pending_osmand_extension_root,
    default_osmand_tiles_root,
    supports_map_extension_download,
    verify_osmand_extension_install,
)

_LOGGER = logging.getLogger(__name__)
_CHUNK_SIZE = 1024 * 256
_DOWNLOAD_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class MapExtensionDownloadRequest:
    package_root: Path
    platform: str


@dataclass(frozen=True)
class MapExtensionDownloadResult:
    pending_root: Path
    extension_root: Path


class MapExtensionDownloadSignals(QObject):
    progress = Signal(int, int, str)
    ready = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class MapExtensionDownloadWorker(QRunnable):
    """Download the published archive and stage it for the next restart."""

    def __init__(self, request_payload: MapExtensionDownloadRequest) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request_payload
        self.signals = MapExtensionDownloadSignals()

    def run(self) -> None:  # pragma: no cover - exercised by focused tests
        try:
            result = self._download_and_stage()
            self.signals.ready.emit(result)
        except Exception as exc:  # noqa: BLE001 - isolate worker failures
            _LOGGER.exception("Failed to download map extension")
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()

    def _download_and_stage(self) -> MapExtensionDownloadResult:
        if not supports_map_extension_download(self._request.platform):
            raise RuntimeError("Map extension download is unavailable on this platform.")

        download_url = default_osmand_download_url(self._request.platform)
        if not download_url:
            raise RuntimeError("Map extension download URL is unavailable.")

        tiles_root = default_osmand_tiles_root(self._request.package_root)
        tiles_root.mkdir(parents=True, exist_ok=True)
        pending_root = default_pending_osmand_extension_root(self._request.package_root)
        extension_root = default_osmand_extension_root(self._request.package_root)

        with tempfile.TemporaryDirectory(prefix="iphoto-map-extension-") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            archive_name = "extension.zip" if self._request.platform == "win32" else "extension.tar.xz"
            archive_path = tmp_dir / archive_name
            extracted_root = tmp_dir / "extracted"
            extracted_root.mkdir(parents=True, exist_ok=True)

            self._download_archive(download_url, archive_path)
            self._extract_archive(archive_path, extracted_root)
            staged_extension_root = extracted_root / "extension"
            self.signals.progress.emit(96, 100, "Validating map extension...")
            self._validate_extension_root(staged_extension_root)
            self._publish_pending_install(staged_extension_root, pending_root)
            self._install_and_verify_pending_root()

        self.signals.progress.emit(100, 100, "Map extension installed.")
        return MapExtensionDownloadResult(
            pending_root=pending_root,
            extension_root=extension_root,
        )

    def _download_archive(self, download_url: str, archive_path: Path) -> None:
        self.signals.progress.emit(0, 0, "Downloading map extension...")
        try:
            with request.urlopen(download_url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, archive_path.open(
                "wb"
            ) as handle:
                total_header = response.headers.get("Content-Length", "").strip()
                try:
                    total = int(total_header)
                except ValueError:
                    total = 0

                received = 0
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    self.signals.progress.emit(
                        received,
                        total,
                        "Downloading map extension...",
                    )
        except TimeoutError as exc:
            raise RuntimeError(
                "Map extension download timed out. Please check your connection and try again."
            ) from exc
        except socket.timeout as exc:
            raise RuntimeError(
                "Map extension download timed out. Please check your connection and try again."
            ) from exc

    def _extract_archive(self, archive_path: Path, extracted_root: Path) -> None:
        self.signals.progress.emit(95, 100, "Extracting map extension...")
        suffixes = archive_path.suffixes
        if suffixes[-2:] == [".tar", ".xz"]:
            with tarfile.open(archive_path, mode="r:xz") as archive:
                archive.extractall(extracted_root)
            return
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extracted_root)
            return
        raise RuntimeError(f"Unsupported map extension archive: {archive_path.name}")

    def _validate_extension_root(self, extension_root: Path) -> None:
        required_paths = (
            extension_root / "World_basemap_2.obf",
            extension_root / "rendering_styles" / "snowmobile.render.xml",
            extension_root / "search" / "geonames.sqlite3",
        )
        for candidate in required_paths:
            if not candidate.exists():
                raise RuntimeError(
                    "Downloaded map extension is incomplete: "
                    f"missing '{candidate.relative_to(extension_root)}'."
                )

        helper_candidates = (
            extension_root / "bin" / "osmand_render_helper.exe",
            extension_root / "bin" / "osmand_render_helper_sdk.exe",
        ) if self._request.platform == "win32" else (
            extension_root / "bin" / "osmand_render_helper",
            extension_root / "bin" / "osmand_render_helper_sdk",
        )
        if not any(candidate.is_file() for candidate in helper_candidates):
            raise RuntimeError("Downloaded map extension is incomplete: helper binary is missing.")

    def _publish_pending_install(self, extension_root: Path, pending_root: Path) -> None:
        self.signals.progress.emit(97, 100, "Staging map extension...")
        if pending_root.exists():
            shutil.rmtree(pending_root)
        shutil.move(str(extension_root), str(pending_root))

    def _install_and_verify_pending_root(self) -> None:
        self.signals.progress.emit(98, 100, "Installing map extension...")
        try:
            apply_pending_osmand_extension_install(self._request.package_root)
        except Exception as exc:
            raise RuntimeError(
                "Map extension files were downloaded, but the install folder could not be activated."
            ) from exc

        self.signals.progress.emit(99, 100, "Verifying installed files...")
        if not verify_osmand_extension_install(
            self._request.package_root,
            platform=self._request.platform,
        ):
            raise RuntimeError(
                "Map extension files were downloaded, but the install folder is still incomplete or stuck as '.pending'."
            )


__all__ = [
    "MapExtensionDownloadRequest",
    "MapExtensionDownloadResult",
    "MapExtensionDownloadSignals",
    "MapExtensionDownloadWorker",
]
