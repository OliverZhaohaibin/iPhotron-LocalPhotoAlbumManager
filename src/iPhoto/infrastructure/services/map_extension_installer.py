"""Network/filesystem adapter for verified, recoverable map installations."""

from __future__ import annotations

import logging
import shutil
import struct
import tarfile
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from iPhoto.application.ports.map_extension import (
    MapExtensionError,
    MapExtensionProgress,
    MapExtensionRequest,
    MapExtensionResult,
)
from maps import map_sources

from .extension_archives import (
    cached_download,
    check_member_path,
    copied_archive,
    download_archive,
    extract_zip,
    file_digest,
    install_failure,
    install_lock,
    match_archive,
    probe_target,
)
from .map_extension_packages import PACKAGES, MapExtensionPackage

_LOG = logging.getLogger(__name__)


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
        _LOG.info(
            "Map component operation=%s platform=%s root=%s",
            payload.operation,
            payload.platform,
            target,
        )
        try:
            if payload.operation == "prepare":
                result = self._prepare_read_only(payload, target)
                if result is not None:
                    return result
            probe_target(target.parent)
            with install_lock(target.parent / ".map-install.lock"):
                if payload.operation == "prepare":
                    return self._prepare(payload, target, progress)
                if payload.local_archive_path is not None:
                    stage = "verify"
                    progress(0, 0, "Validating map extension...")
                    with copied_archive(
                        payload.local_archive_path, target.parent, prefix=".map-import-"
                    ) as archive:
                        self._verify_archive(archive, payload.platform)
                        stage = "extract"
                        return self._stage_archive(payload, target, archive, progress)
                stage = "download"
                archive = self._obtain_archive(payload, target, progress)
                stage = "extract"
                result = self._stage_archive(payload, target, archive, progress)
                # Retain a verified download on failure or deferred activation.
                if result.status == "installed":
                    self._cleanup(archive)
                return result
        except MapExtensionError as failure:
            _LOG.warning("Map component failure: %s", failure)
            raise
        except Exception as exc:
            failure = install_failure(exc, stage, f"install_root={target}")
            _LOG.warning("Map component failure: %s", failure)
            raise failure from exc

    def _obtain_archive(self, payload, target, progress) -> Path:
        package = next(iter(self._supported_packages(payload.platform)), None)
        if package is None:
            raise MapExtensionError("unsupported", "download")
        return cached_download(
            target.parent,
            package,
            lambda partial: self._download(package, partial, progress),
            lambda archive: self._verify_archive(archive, payload.platform, stage="download"),
        )

    def _download(self, package, partial, progress):
        download_archive(
            package, partial, progress, "Downloading map extension...", "iPhotron-map-installer/1"
        )

    def _verify_archive(self, archive: Path, platform: str, *, stage: str = "verify") -> None:
        match = match_archive(archive, self._packages, stage=stage)
        if match not in self._supported_packages(platform):
            raise MapExtensionError("unsupported", stage)

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

    def _extract(self, archive: Path, directory: Path):
        if zipfile.is_zipfile(archive):
            extract_zip(archive, directory, root="extension")
        else:
            with tarfile.open(archive, "r:*") as tar:
                for member in tar.getmembers():
                    check_member_path(member.name, root="extension")
                # Native macOS bundles may contain internal relative links.
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
            raise install_failure(exc, "activate", f"install_root={target}") from exc
        self._validate(target, payload.platform)
        return MapExtensionResult(pending, target)

    def _prepare_read_only(
        self, payload: MapExtensionRequest, target: Path
    ) -> MapExtensionResult | None:
        """Return a runtime status when there is no installation work to do."""
        pending = target.with_name(target.name + ".pending")
        previous = target.with_name(target.name + ".pending.previous")
        backup = target.with_name(target.name + ".backup")
        if (
            pending.exists()
            or previous.exists()
            or (not target.exists() and backup.exists())
            or self._legacy_pending_root(payload, target, pending) is not None
        ):
            return None
        if map_sources.has_installed_osmand_extension(payload.package_root):
            return MapExtensionResult(
                pending, map_sources.default_osmand_extension_root(payload.package_root)
            )
        if map_sources.bundled_osmand_extension_archive(payload.package_root) is None:
            return MapExtensionResult(pending, target, "missing")
        return None

    @staticmethod
    def _legacy_pending_root(
        payload: MapExtensionRequest, target: Path, pending: Path
    ) -> Path | None:
        legacy = Path(payload.package_root) / "tiles" / "extension.pending"
        if (
            legacy != pending
            and legacy.is_dir()
            and not map_sources.validate_osmand_extension_root(target, platform=payload.platform)
        ):
            return legacy
        return None

    def _prepare(self, payload, target, progress):
        pending = target.with_name(target.name + ".pending")
        previous = target.with_name(target.name + ".pending.previous")
        if not pending.exists() and previous.exists():
            previous.replace(pending)
        backup = target.with_name(target.name + ".backup")
        if not target.exists() and backup.exists():
            # Recover a process exit between the two directory renames.
            try:
                self._validate(backup, payload.platform)
            except MapExtensionError:
                if not pending.exists():
                    raise
            else:
                backup.replace(target)
        legacy = self._legacy_pending_root(payload, target, pending) if not pending.exists() else None
        if legacy is not None:
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
            if not checksum.is_file() or checksum.read_text().strip() != file_digest(bundled):
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
