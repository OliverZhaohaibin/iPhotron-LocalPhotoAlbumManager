"""Backend abstractions for vector and raster map tile sources."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, TypeAlias

from PySide6.QtCore import QProcess, QProcessEnvironment, QStandardPaths
from PySide6.QtGui import QImage

from maps.map_sources import MapBackendMetadata, MapSourceSpec
from maps.tile_parser import TileAccessError, TileLoadingError, TileParser

LOGGER = logging.getLogger(__name__)

if sys.platform == "win32":
    DEFAULT_QT_ROOT = Path(r"C:\Qt\6.10.1\mingw_64")
    DEFAULT_MINGW_ROOT = Path(r"C:\Qt\Tools\mingw1310_64")
else:
    DEFAULT_QT_ROOT = Path("/usr")
    DEFAULT_MINGW_ROOT = Path()
ENV_QT_ROOT = "IPHOTO_OSMAND_QT_ROOT"
ENV_MINGW_ROOT = "IPHOTO_OSMAND_MINGW_ROOT"
DEFAULT_HELPER_INIT_TIMEOUT_MS = 30000
DEFAULT_HELPER_RENDER_TIMEOUT_MS = 30000


def _startup_profile_enabled() -> bool:
    return os.environ.get("IPHOTO_OSMAND_PROFILE_STARTUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _log_startup_profile(stage: str, elapsed_ms: float, **details: object) -> None:
    if not _startup_profile_enabled():
        return

    suffix = ""
    if details:
        parts = [f"{key}={value}" for key, value in details.items()]
        suffix = " " + " ".join(parts)
    LOGGER.info("[osmand_helper][startup] %s %.1fms%s", stage, elapsed_ms, suffix)


class TileBackendUnavailableError(TileLoadingError):
    """Raised when a tile backend cannot currently serve requests."""


class TileRenderError(TileLoadingError):
    """Raised when a raster tile could not be rendered correctly."""


@dataclass(frozen=True)
class RasterTile:
    """Represent a rendered raster tile returned by an OBF backend."""

    image: QImage
    device_scale: float = 1.0


VectorTilePayload: TypeAlias = Dict[str, dict]
TilePayload: TypeAlias = VectorTilePayload | RasterTile


class TileBackend(Protocol):
    """Protocol shared by map tile backends."""

    metadata: MapBackendMetadata

    def probe(self) -> MapBackendMetadata:
        """Validate the backend and return its runtime metadata."""

    def load_tile(self, z: int, x: int, y: int) -> Optional[TilePayload]:
        """Return the requested tile payload or ``None`` when unavailable."""

    def clear_cache(self) -> None:
        """Drop any in-memory tile caches maintained by the backend."""

    def shutdown(self) -> None:
        """Release long-lived resources such as helper processes."""

    def set_device_scale(self, scale: float) -> None:
        """Update the desired raster device scale for future requests."""


class LegacyVectorBackend:
    """Wrap the existing local Mapbox vector-tile directory."""

    INTERACTIVE_MIN_ZOOM = 2.0
    INTERACTIVE_MAX_ZOOM = 8.5
    DEFAULT_FETCH_MAX_ZOOM = 6

    def __init__(self, source: MapSourceSpec) -> None:
        if source.kind != "legacy_pbf":
            raise ValueError("LegacyVectorBackend requires a legacy_pbf source")
        self._source = source
        self._parser = TileParser(Path(source.data_path))
        self.style_path = Path(source.style_path or "style.json")
        self.metadata = self._load_metadata()

    def probe(self) -> MapBackendMetadata:
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> Optional[VectorTilePayload]:
        return self._parser.load_tile(z, x, y)

    def clear_cache(self) -> None:
        self._parser.clear_cache()

    def shutdown(self) -> None:
        return None

    def set_device_scale(self, scale: float) -> None:
        return None

    def _load_metadata(self) -> MapBackendMetadata:
        tiles_json = Path(self._source.data_path) / "tiles.json"
        if tiles_json.exists():
            try:
                raw = json.loads(tiles_json.read_text(encoding="utf8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            # Preserve the pre-OBF interaction range for the legacy fallback
            # while still fetching only from the tile levels that exist on disk.
            try:
                fetch_max_zoom = max(0, int(float(raw.get("maxzoom", self.DEFAULT_FETCH_MAX_ZOOM))))
            except (TypeError, ValueError):
                fetch_max_zoom = self.DEFAULT_FETCH_MAX_ZOOM
            return MapBackendMetadata(
                min_zoom=self.INTERACTIVE_MIN_ZOOM,
                max_zoom=self.INTERACTIVE_MAX_ZOOM,
                provides_place_labels=False,
                tile_kind="vector",
                tile_scheme="tms",
                fetch_max_zoom=fetch_max_zoom,
            )

        return MapBackendMetadata(
            min_zoom=self.INTERACTIVE_MIN_ZOOM,
            max_zoom=self.INTERACTIVE_MAX_ZOOM,
            provides_place_labels=False,
            tile_kind="vector",
            tile_scheme="tms",
            fetch_max_zoom=self.DEFAULT_FETCH_MAX_ZOOM,
        )


class OsmAndRasterBackend:
    """Load map tiles through an external OsmAnd-compatible helper."""

    CACHE_SCHEMA_VERSION = "2"
    DEFAULT_METADATA = MapBackendMetadata(
        min_zoom=2.0,
        max_zoom=19.0,
        provides_place_labels=True,
        tile_kind="raster",
        tile_scheme="xyz",
        fetch_max_zoom=19,
    )

    def __init__(self, source: MapSourceSpec) -> None:
        if source.kind != "osmand_obf":
            raise ValueError("OsmAndRasterBackend requires an osmand_obf source")
        self._source = source
        self.metadata = self.DEFAULT_METADATA
        self._device_scale = 1.0
        self._process: QProcess | None = None
        self._cache_root: Path | None = None

    def probe(self) -> MapBackendMetadata:
        self._validate_paths()
        return self.metadata

    def probe_runtime(self) -> MapBackendMetadata:
        """Start the helper once in the current thread for diagnostics only."""

        self._validate_paths()
        self._ensure_process()
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> Optional[RasterTile]:
        self._validate_paths()
        cache_path = self._cache_file_path(z, x, y)
        if cache_path.exists():
            return self._load_cached_tile(cache_path)

        for attempt in range(2):
            try:
                self._render_tile_to_cache(z, x, y, cache_path)
                break
            except TileBackendUnavailableError:
                self._remove_partial_cache_file(cache_path)
                if attempt >= 1:
                    raise
                LOGGER.warning(
                    "OsmAnd helper became unavailable while rendering %s/%s/%s; restarting and retrying once",
                    z,
                    x,
                    y,
                )
                self.shutdown()

        return self._load_cached_tile(cache_path)

    def clear_cache(self) -> None:
        return None

    def shutdown(self) -> None:
        if self._process is None:
            return

        try:
            self._communicate(self._process, {"command": "shutdown"}, timeout_ms=1000)
        except TileLoadingError:
            pass

        self._process.kill()
        self._process.waitForFinished(1000)
        self._process = None

    def set_device_scale(self, scale: float) -> None:
        self._device_scale = max(1.0, float(scale))

    def _ensure_process(self) -> QProcess:
        if self._process is not None and self._process.state() == QProcess.ProcessState.Running:
            return self._process

        command = self._helper_command()
        if not command:
            raise TileBackendUnavailableError(
                "OsmAnd helper command not configured. Set IPHOTO_OSMAND_RENDER_HELPER.",
            )

        total_started = time.perf_counter()
        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedErrorChannel)
        process.setProgram(command[0])
        process.setArguments(list(command[1:]))
        process.setProcessEnvironment(_helper_process_environment(Path(command[0])))
        start_wait_started = time.perf_counter()
        process.start()
        if not process.waitForStarted(5000):
            stderr = bytes(process.readAllStandardError()).decode("utf8", errors="replace").strip()
            detail = f": {stderr}" if stderr else ""
            raise TileBackendUnavailableError(
                f"Unable to start OsmAnd helper '{command[0]}'{detail}",
            )
        _log_startup_profile(
            "helper_waitForStarted",
            (time.perf_counter() - start_wait_started) * 1000.0,
            command=command[0],
        )

        init_started = time.perf_counter()
        response = self._communicate(
            process,
            {
                "command": "init",
                "obf_path": str(self._source.data_path),
                "resources_root": str(self._source.resources_root),
                "style_path": str(self._source.style_path),
                "night_mode": False,
            },
            timeout_ms=DEFAULT_HELPER_INIT_TIMEOUT_MS,
        )
        _log_startup_profile(
            "helper_init",
            (time.perf_counter() - init_started) * 1000.0,
            source=self._source.data_path,
        )
        if response.get("status") != "ok":
            process.kill()
            process.waitForFinished(1000)
            message = str(response.get("message", "failed to initialise OsmAnd helper"))
            raise TileBackendUnavailableError(message)

        self.metadata = MapBackendMetadata(
            min_zoom=float(response.get("min_zoom", self.DEFAULT_METADATA.min_zoom)),
            max_zoom=float(response.get("max_zoom", self.DEFAULT_METADATA.max_zoom)),
            provides_place_labels=bool(
                response.get(
                    "provides_place_labels",
                    self.DEFAULT_METADATA.provides_place_labels,
                ),
            ),
            tile_kind="raster",
            tile_scheme="xyz",
            fetch_max_zoom=max(
                0,
                int(float(response.get("max_zoom", self.DEFAULT_METADATA.max_zoom))),
            ),
        )
        self._process = process
        _log_startup_profile(
            "helper_total_startup",
            (time.perf_counter() - total_started) * 1000.0,
            source=self._source.data_path,
        )
        return process

    def _communicate(
        self,
        process: QProcess,
        payload: dict[str, object],
        *,
        timeout_ms: int = 5000,
    ) -> dict[str, object]:
        message = json.dumps(payload, ensure_ascii=True) + "\n"
        process.write(message.encode("utf8"))
        if not process.waitForBytesWritten(timeout_ms):
            self._discard_process(process)
            raise TileBackendUnavailableError("Timed out while writing to the OsmAnd helper")

        if not process.canReadLine() and not process.waitForReadyRead(timeout_ms):
            self._discard_process(process)
            raise TileBackendUnavailableError("Timed out while waiting for the OsmAnd helper")

        while not process.canReadLine():
            if not process.waitForReadyRead(timeout_ms):
                self._discard_process(process)
                raise TileBackendUnavailableError("Timed out while reading from the OsmAnd helper")

        raw_line = bytes(process.readLine()).decode("utf8", errors="replace").strip()
        if not raw_line:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned an empty response")

        try:
            response = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            self._discard_process(process)
            raise TileBackendUnavailableError(
                f"OsmAnd helper returned invalid JSON: {raw_line!r}",
            ) from exc

        if not isinstance(response, dict):
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned a non-object JSON response")

        return response

    def _validate_paths(self) -> None:
        data_path = Path(self._source.data_path)
        if not data_path.exists():
            raise TileAccessError(f"OBF file '{data_path}' does not exist")
        resources_root = Path(self._source.resources_root or "")
        if not resources_root.exists():
            raise TileAccessError(f"OsmAnd resources directory '{resources_root}' does not exist")
        style_path = Path(self._source.style_path or "")
        if not style_path.exists():
            raise TileAccessError(f"OsmAnd rendering style '{style_path}' does not exist")

    def _helper_command(self) -> tuple[str, ...] | None:
        return self._source.helper_command

    def _render_tile_to_cache(self, z: int, x: int, y: int, cache_path: Path) -> None:
        process = self._ensure_process()
        request = {
            "command": "render",
            "z": int(z),
            "x": int(x),
            "y": int(y),
            "device_scale": float(self._device_scale),
            "output_path": str(cache_path),
        }
        render_started = time.perf_counter()
        response = self._communicate(process, request, timeout_ms=DEFAULT_HELPER_RENDER_TIMEOUT_MS)
        _log_startup_profile(
            "helper_render",
            (time.perf_counter() - render_started) * 1000.0,
            z=z,
            x=x,
            y=y,
        )
        if response.get("status") != "ok":
            message = str(response.get("message", "unknown render failure"))
            raise TileRenderError(message)

        if not cache_path.exists():
            raise TileRenderError(f"OsmAnd helper reported success but '{cache_path}' was not created")

    def _discard_process(self, process: QProcess) -> None:
        if self._process is process:
            self._process = None
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1000)

    def _remove_partial_cache_file(self, cache_path: Path) -> None:
        try:
            cache_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.debug("Failed to remove partial cache file '%s'", cache_path, exc_info=True)

    def _cache_file_path(self, z: int, x: int, y: int) -> Path:
        cache_root = self._cache_directory()
        scale_tag = f"{self._device_scale:.2f}".replace(".", "_")
        cache_path = cache_root / scale_tag / str(z) / str(x)
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path / f"{y}.png"

    def _cache_directory(self) -> Path:
        if self._cache_root is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            if not base:
                base = str(Path(tempfile.gettempdir()) / "iPhoto")
            fingerprint = hashlib.sha256(
                "|".join(
                    (
                        self.CACHE_SCHEMA_VERSION,
                        str(self._source.data_path),
                        str(self._source.style_path),
                        str(self._source.resources_root),
                        "|".join(self._source.helper_command or ()),
                    ),
                ).encode("utf8"),
            ).hexdigest()[:16]
            self._cache_root = Path(base) / "maps" / "obf" / fingerprint
        return self._cache_root

    def _load_cached_tile(self, path: Path) -> RasterTile:
        image = QImage(str(path))
        if image.isNull():
            raise TileRenderError(f"Unable to decode raster tile '{path}'")
        return RasterTile(image=image, device_scale=self._device_scale)


def _helper_process_environment(helper_executable: Path) -> QProcessEnvironment:
    """Return a process environment that can load the helper's native libraries."""

    env = QProcessEnvironment.systemEnvironment()
    path_entries = _existing_path_entries(env)
    prepended_entries = [str(path) for path in _helper_runtime_paths(helper_executable)]

    merged_path: list[str] = []
    seen: set[str] = set()
    for entry in (*prepended_entries, *path_entries):
        normalized = entry.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged_path.append(normalized)

    env.insert("PATH", os.pathsep.join(merged_path))
    if os.name != "nt":
        ld_key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        existing_ld = env.value(ld_key, "")
        ld_entries = [entry for entry in existing_ld.split(os.pathsep) if entry]
        merged_ld: list[str] = []
        seen_ld: set[str] = set()
        for entry in (*prepended_entries, *ld_entries):
            normalized = entry.strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen_ld:
                continue
            seen_ld.add(key)
            merged_ld.append(normalized)
        env.insert(ld_key, os.pathsep.join(merged_ld))
    return env


def _helper_runtime_paths(helper_executable: Path) -> tuple[Path, ...]:
    """Return runtime search paths for helper-side Qt and toolchain libraries."""

    candidates: list[Path] = [helper_executable.resolve().parent]

    for root in (
        os.environ.get(ENV_QT_ROOT),
        os.environ.get("QT_ROOT"),
        os.environ.get("QTDIR"),
        str(DEFAULT_QT_ROOT),
    ):
        candidates.extend(_runtime_bin_candidates(root))

    for root in (
        os.environ.get(ENV_MINGW_ROOT),
        os.environ.get("MINGW_ROOT"),
        str(DEFAULT_MINGW_ROOT),
    ):
        candidates.extend(_runtime_bin_candidates(root))

    if os.name != "nt":
        try:
            import PySide6
        except ImportError:  # pragma: no cover - dependency is required in production
            pass
        else:
            pyside_root = Path(PySide6.__file__).resolve().parent
            candidates.append(pyside_root)
            candidates.extend(_runtime_bin_candidates(str((pyside_root / "Qt" / "lib").resolve())))

    existing: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        existing.append(resolved)

    return tuple(existing)


def _runtime_bin_candidates(root: str | None) -> tuple[Path, ...]:
    """Expand a Qt/MinGW root into candidate runtime directories."""

    if not root:
        return ()

    path = Path(root)
    if path.name.lower() == "bin":
        return (path,)
    return (path / "bin", path)


def _existing_path_entries(env: QProcessEnvironment) -> tuple[str, ...]:
    """Return the current process PATH split into individual entries."""

    path_value = env.value("PATH", "")
    if not path_value:
        return ()
    return tuple(entry for entry in path_value.split(os.pathsep) if entry)


class FallbackTileBackend:
    """Try a preferred backend first and fall back to a secondary backend."""

    def __init__(self, primary: TileBackend, fallback: TileBackend) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_enabled = False
        self.metadata = fallback.probe()

        try:
            self.metadata = primary.probe()
            self._primary_enabled = True
        except TileLoadingError as exc:
            LOGGER.warning("Primary map backend unavailable, using legacy fallback: %s", exc)

    def probe(self) -> MapBackendMetadata:
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> Optional[TilePayload]:
        if self._primary_enabled:
            try:
                tile = self._primary.load_tile(z, x, y)
                if tile is not None:
                    return tile
            except TileBackendUnavailableError as exc:
                LOGGER.warning(
                    "Primary map backend disabled after runtime failure on %s/%s/%s: %s",
                    z,
                    x,
                    y,
                    exc,
                )
                self._primary_enabled = False
                try:
                    self._primary.shutdown()
                except Exception:  # pragma: no cover - best effort cleanup only
                    LOGGER.debug("Primary backend shutdown failed after runtime error", exc_info=True)
                self.metadata = self._fallback.probe()
            except TileLoadingError as exc:
                LOGGER.warning(
                    "Primary map backend failed for %s/%s/%s, falling back to legacy tiles: %s",
                    z,
                    x,
                    y,
                    exc,
                )

        return self._fallback.load_tile(z, x, y)

    def clear_cache(self) -> None:
        self._primary.clear_cache()
        self._fallback.clear_cache()

    def shutdown(self) -> None:
        self._primary.shutdown()
        self._fallback.shutdown()

    def set_device_scale(self, scale: float) -> None:
        self._primary.set_device_scale(scale)
        self._fallback.set_device_scale(scale)


__all__ = [
    "FallbackTileBackend",
    "LegacyVectorBackend",
    "OsmAndRasterBackend",
    "RasterTile",
    "TileBackend",
    "TileBackendUnavailableError",
    "TilePayload",
    "TileRenderError",
    "VectorTilePayload",
]
