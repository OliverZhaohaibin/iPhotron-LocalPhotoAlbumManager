"""Definitions for selecting and describing map data sources."""

from __future__ import annotations

import os
import shutil
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT = Path("tiles") / "extension"
DEFAULT_OSMAND_OBF_FILENAME = "World_basemap_2.obf"
DEFAULT_OSMAND_STYLE_FILENAME = "snowmobile.render.xml"
DEFAULT_OSMAND_RESOURCES_ROOT = DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT
DEFAULT_OSMAND_STYLE_PATH = DEFAULT_OSMAND_RESOURCES_ROOT / "rendering_styles" / DEFAULT_OSMAND_STYLE_FILENAME
DEFAULT_OSMAND_SEARCH_RELATIVE_PATH = DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "search" / "geonames.sqlite3"
DEFAULT_OSMAND_PENDING_EXTENSION_SUFFIX = ".pending"
LINUX_MAP_EXTENSION_DOWNLOAD_URL = (
    "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
    "releases/download/v5.0.0/extension.tar.xz"
)
WINDOWS_MAP_EXTENSION_DOWNLOAD_URL = (
    "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
    "releases/download/v5.0.0/extension.zip"
)
ENV_OSMAND_HELPER = "IPHOTO_OSMAND_RENDER_HELPER"
ENV_OSMAND_NATIVE_WIDGET_LIBRARY = "IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY"
ENV_OSMAND_EXTENSION_ROOT = "IPHOTO_OSMAND_EXTENSION_ROOT"
ENV_PREFER_OSMAND_NATIVE_WIDGET = "IPHOTO_PREFER_OSMAND_NATIVE_WIDGET"
if sys.platform == "win32":
    DEFAULT_HELPER_RELATIVE_PATH = DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_render_helper.exe"
    DEFAULT_HELPER_RELATIVE_PATHS = (
        DEFAULT_HELPER_RELATIVE_PATH,
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_render_helper_sdk.exe",
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_native_widget.dll"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH = DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "libosmand_native_widget.dll"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS = (
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC,
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW,
    )
    SDK_HELPER_RELATIVE_PATHS: tuple[Path, ...] = ()
    SDK_NATIVE_WIDGET_RELATIVE_PATHS: tuple[Path, ...] = ()
elif sys.platform == "darwin":
    DEFAULT_HELPER_RELATIVE_PATH = DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_render_helper"
    DEFAULT_HELPER_RELATIVE_PATHS = (DEFAULT_HELPER_RELATIVE_PATH,)
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_native_widget.dylib"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC = DEFAULT_NATIVE_WIDGET_RELATIVE_PATH
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "libosmand_native_widget.dylib"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS = (
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH,
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW,
    )
    SDK_HELPER_RELATIVE_PATHS = (
        Path("tools") / "osmand_render_helper_native" / "dist-macosx" / "osmand_render_helper",
    )
    SDK_NATIVE_WIDGET_RELATIVE_PATHS = (
        Path("tools") / "osmand_render_helper_native" / "dist-macosx" / "osmand_native_widget.dylib",
        Path("tools") / "osmand_render_helper_native" / "dist-macosx" / "libosmand_native_widget.dylib",
    )
else:
    DEFAULT_HELPER_RELATIVE_PATH = DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_render_helper"
    DEFAULT_HELPER_RELATIVE_PATHS = (
        DEFAULT_HELPER_RELATIVE_PATH,
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_render_helper_sdk",
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "osmand_native_widget.so"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC = DEFAULT_NATIVE_WIDGET_RELATIVE_PATH
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW = (
        DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT / "bin" / "libosmand_native_widget.so"
    )
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS = (
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH,
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW,
    )
    SDK_HELPER_RELATIVE_PATHS = (
        Path("tools") / "osmand_render_helper_native" / "dist-linux" / "osmand_render_helper",
    )
    SDK_NATIVE_WIDGET_RELATIVE_PATHS = (
        Path("tools") / "osmand_render_helper_native" / "dist-linux" / "osmand_native_widget.so",
        Path("tools") / "osmand_render_helper_native" / "dist-linux" / "libosmand_native_widget.so",
    )
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def prefer_osmand_native_widget() -> bool:
    """Return whether auto-selection should prefer the native OsmAnd widget.

    The native widget stays enabled by default so packaged builds can use the
    fully OpenGL-backed OBF renderer. Set
    ``IPHOTO_PREFER_OSMAND_NATIVE_WIDGET=0`` to force the Python OBF path.
    """

    raw_value = os.environ.get(ENV_PREFER_OSMAND_NATIVE_WIDGET, "").strip().lower()
    if raw_value in _TRUE_ENV_VALUES:
        return True
    if raw_value in _FALSE_ENV_VALUES:
        return False
    return True


@dataclass(frozen=True)
class MapBackendMetadata:
    """Describe the capabilities of a concrete map backend."""

    min_zoom: float
    max_zoom: float
    provides_place_labels: bool
    tile_kind: Literal["vector", "raster"]
    tile_scheme: Literal["tms", "xyz"] = "tms"
    fetch_max_zoom: int | None = None


@dataclass(frozen=True)
class MapSourceSpec:
    """Describe how the map should obtain its background data."""

    kind: Literal["legacy_pbf", "osmand_obf"]
    data_path: Path | str
    resources_root: Path | str | None = None
    style_path: Path | str | None = None
    helper_command: tuple[str, ...] | None = None

    def resolved(self, package_root: Path) -> "MapSourceSpec":
        """Return a copy whose filesystem paths are absolute."""

        data_path = _resolve_path(self.data_path, package_root)
        resources_root = _resolve_optional_path(self.resources_root, package_root)
        style_path = _resolve_optional_path(self.style_path, package_root)
        helper_command = self.helper_command or resolve_osmand_helper_command(package_root)
        return MapSourceSpec(
            kind=self.kind,
            data_path=data_path,
            resources_root=resources_root,
            style_path=style_path,
            helper_command=helper_command,
        )

    @classmethod
    def legacy_default(cls, package_root: Path | None = None) -> "MapSourceSpec":
        """Return the bundled vector-tile source."""

        root = package_root or _package_root()
        return cls(
            kind="legacy_pbf",
            data_path=root / "tiles",
            style_path=root / "style.json",
        )

    @classmethod
    def osmand_default(cls, package_root: Path | None = None) -> "MapSourceSpec":
        """Return the default OBF source backed by OsmAnd resources."""

        root = package_root or _package_root()
        extension_root = default_osmand_extension_root(root)
        return cls(
            kind="osmand_obf",
            data_path=extension_root / DEFAULT_OSMAND_OBF_FILENAME,
            resources_root=extension_root,
            style_path=extension_root / "rendering_styles" / DEFAULT_OSMAND_STYLE_FILENAME,
        )

    @classmethod
    def default(cls, package_root: Path | None = None) -> "MapSourceSpec":
        """Prefer the bundled OBF source when the required assets are present."""

        root = package_root or _package_root()
        osmand = cls.osmand_default(root)
        if _has_osmand_data_assets(root):
            return osmand
        return cls.legacy_default(root)


def resolve_osmand_helper_command(package_root: Path | None = None) -> tuple[str, ...] | None:
    """Return the helper command declared via the environment, if any."""

    raw_value = os.environ.get(ENV_OSMAND_HELPER, "").strip()
    if not raw_value:
        root = package_root or _package_root()
        for candidate in _default_helper_candidates(root):
            if candidate.exists():
                return (str(candidate),)
        return None

    parts = tuple(part for part in shlex.split(raw_value, posix=False) if part)
    return parts or None


def resolve_osmand_native_widget_library(package_root: Path | None = None) -> Path | None:
    """Return the native Qt widget library path when it is available."""

    raw_value = os.environ.get(ENV_OSMAND_NATIVE_WIDGET_LIBRARY, "").strip()
    if raw_value:
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            candidate = (package_root or _package_root()) / candidate
        return candidate if candidate.exists() else None

    root = package_root or _package_root()
    for candidate in _default_native_widget_candidates(root):
        if candidate.exists():
            return candidate
    return None


def has_usable_osmand_default(package_root: Path | None = None) -> bool:
    """Return ``True`` when the bundled OBF source and helper are both available."""

    root = package_root or _package_root()
    source = MapSourceSpec.osmand_default(root).resolved(root)
    return _has_osmand_data_assets(root) and bool(source.helper_command)


def has_usable_osmand_native_widget(package_root: Path | None = None) -> bool:
    """Return ``True`` when the bundled OBF source and native widget library are available."""

    root = package_root or _package_root()
    return _has_osmand_data_assets(root) and resolve_osmand_native_widget_library(root) is not None


def default_osmand_search_database(package_root: Path | None = None) -> Path:
    """Return the bundled GeoNames database used by offline place search."""

    root = package_root or _package_root()
    return (default_osmand_extension_root(root) / "search" / "geonames.sqlite3").resolve()


def default_osmand_tiles_root(package_root: Path | None = None) -> Path:
    """Return the tiles root that hosts both legacy and extension map assets."""

    root = package_root or _package_root()
    return _managed_osmand_extension_root(root).parent.resolve()


def has_usable_osmand_search_extension(package_root: Path | None = None) -> bool:
    """Return ``True`` when both the map assets and search DB are bundled."""

    root = package_root or _package_root()
    return _has_osmand_data_assets(root) and default_osmand_search_database(root).is_file()


def default_pending_osmand_extension_root(package_root: Path | None = None) -> Path:
    """Return the staging directory consumed on the next application launch."""

    root = package_root or _package_root()
    extension_root = _managed_osmand_extension_root(root)
    return extension_root.with_name(extension_root.name + DEFAULT_OSMAND_PENDING_EXTENSION_SUFFIX)


def default_osmand_download_url(platform: str | None = None) -> str | None:
    """Return the published extension archive URL for *platform*."""

    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform == "win32":
        return WINDOWS_MAP_EXTENSION_DOWNLOAD_URL
    if resolved_platform.startswith("linux"):
        return LINUX_MAP_EXTENSION_DOWNLOAD_URL
    return None


def supports_map_extension_download(platform: str | None = None) -> bool:
    """Return whether the current platform offers a published extension archive."""

    return default_osmand_download_url(platform) is not None


def has_pending_osmand_extension_install(package_root: Path | None = None) -> bool:
    """Return ``True`` when a staged extension is waiting for restart."""

    pending_root = default_pending_osmand_extension_root(package_root)
    return pending_root.is_dir()


def has_installed_osmand_extension(package_root: Path | None = None) -> bool:
    """Return ``True`` when the packaged extension layout is complete."""

    root = package_root or _package_root()
    extension_root = default_osmand_extension_root(root)

    return validate_osmand_extension_root(extension_root, platform=sys.platform)


def validate_osmand_extension_root(extension_root: Path, *, platform: str | None = None) -> bool:
    """Return ``True`` when *extension_root* contains a complete runtime."""

    resolved_platform = sys.platform if platform is None else platform
    required_paths = (
        extension_root / DEFAULT_OSMAND_OBF_FILENAME,
        extension_root / "rendering_styles" / DEFAULT_OSMAND_STYLE_FILENAME,
        extension_root / "search" / "geonames.sqlite3",
    )
    if not extension_root.is_dir() or not all(candidate.exists() for candidate in required_paths):
        return False

    if resolved_platform == "win32":
        helper_candidates = (
            extension_root / "bin" / "osmand_render_helper.exe",
            extension_root / "bin" / "osmand_render_helper_sdk.exe",
        )
    else:
        helper_candidates = (
            extension_root / "bin" / "osmand_render_helper",
            extension_root / "bin" / "osmand_render_helper_sdk",
        )
    return any(candidate.is_file() for candidate in helper_candidates)


def verify_osmand_extension_install(package_root: Path | None = None, *, platform: str | None = None) -> bool:
    """Return ``True`` when the active extension is complete and no pending dir remains."""

    root = package_root or _package_root()
    managed_extension_root = _managed_osmand_extension_root(root)
    return (
        not has_pending_osmand_extension_install(root)
        and validate_osmand_extension_root(
            managed_extension_root,
            platform=platform,
        )
    )


def apply_pending_osmand_extension_install(package_root: Path | None = None) -> bool:
    """Promote a staged extension into place.

    Returns ``True`` when a pending install existed and was promoted.
    """

    root = package_root or _package_root()
    pending_root = default_pending_osmand_extension_root(root)
    if not pending_root.exists():
        return False

    extension_root = _managed_osmand_extension_root(root)
    backup_root = extension_root.with_name(extension_root.name + ".backup")

    if backup_root.exists():
        if backup_root.is_dir():
            shutil.rmtree(backup_root)
        else:
            backup_root.unlink()

    if extension_root.exists():
        extension_root.replace(backup_root)

    try:
        pending_root.replace(extension_root)
    except Exception:
        if backup_root.exists() and not extension_root.exists():
            backup_root.replace(extension_root)
        raise
    else:
        if backup_root.exists():
            shutil.rmtree(backup_root)
    return True


def _has_osmand_data_assets(package_root: Path) -> bool:
    source = MapSourceSpec.osmand_default(package_root).resolved(package_root)
    return (
        Path(source.data_path).exists()
        and Path(source.resources_root or "").exists()
        and Path(source.style_path or "").exists()
    )


def _resolve_path(value: Path | str, package_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = package_root / path
    return path


def _resolve_optional_path(value: Path | str | None, package_root: Path) -> Path | None:
    if value is None:
        return None
    return _resolve_path(value, package_root)


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root(package_root: Path | None = None) -> Path:
    root = package_root or _package_root()
    return Path(root).resolve().parent.parent


def default_osmand_extension_root(package_root: Path | None = None) -> Path:
    """Return the self-contained extension directory used for OBF resources."""

    root = package_root or _package_root()
    bundled_root = _bundled_osmand_extension_root(root)
    managed_root = _managed_osmand_extension_root(root)
    if managed_root == bundled_root:
        return bundled_root
    if managed_root.exists() or not bundled_root.exists():
        return managed_root
    return bundled_root


def _bundled_osmand_extension_root(package_root: Path) -> Path:
    return (Path(package_root) / DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT).resolve()


def _managed_osmand_extension_root(package_root: Path) -> Path:
    root = Path(package_root).resolve()
    override_root = os.environ.get(ENV_OSMAND_EXTENSION_ROOT, "").strip()
    if override_root:
        return Path(override_root).expanduser().resolve()
    if _should_use_external_osmand_extension_root(root):
        return _default_external_osmand_extension_root()
    return _bundled_osmand_extension_root(root)


def _sdk_roots(repo_root: Path) -> tuple[Path, ...]:
    """Return candidate PySide6-OsmAnd-SDK checkout paths in preference order.

    The SDK may live either inside the repository root (e.g. a git submodule or
    a manual checkout into the project tree) or as a sibling directory next to
    the repository.  Both locations are checked so that developers are not
    required to put the checkout in a specific place.
    """
    inner = repo_root / "PySide6-OsmAnd-SDK"
    sibling = repo_root.parent / "PySide6-OsmAnd-SDK"
    return tuple(p for p in (inner, sibling) if p.exists())


def _default_helper_candidates(package_root: Path) -> tuple[Path, ...]:
    normalized_root = Path(package_root).resolve()
    repo_root = _repo_root(normalized_root)
    sdk_roots = _sdk_roots(repo_root)
    sdk_candidates = _collect_candidate_paths(sdk_roots, SDK_HELPER_RELATIVE_PATHS) if sdk_roots else ()
    local_candidates = _collect_candidate_paths(
        _default_osmand_search_roots(normalized_root),
        DEFAULT_HELPER_RELATIVE_PATHS,
    )
    return _dedupe_candidates(sdk_candidates + local_candidates)


def _default_native_widget_candidates(package_root: Path) -> tuple[Path, ...]:
    normalized_root = Path(package_root).resolve()
    repo_root = _repo_root(normalized_root)
    sdk_roots = _sdk_roots(repo_root)
    sdk_candidates: tuple[Path, ...] = ()
    if sys.platform != "win32" and sdk_roots:
        sdk_candidates = _collect_candidate_paths(sdk_roots, SDK_NATIVE_WIDGET_RELATIVE_PATHS)
    local_candidates = _collect_candidate_paths(
        _default_osmand_search_roots(normalized_root),
        DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS,
    )
    return _dedupe_candidates(sdk_candidates + local_candidates)


def _default_external_osmand_extension_root() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if os.name == "nt":
        base = os.environ.get("APPDATA", "").strip()
        if base:
            return (Path(base) / "iPhoto" / "maps" / "tiles" / "extension").resolve()
        return (Path.home() / "AppData" / "Roaming" / "iPhoto" / "maps" / "tiles" / "extension").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "iPhoto" / "maps" / "tiles" / "extension").resolve()
    if data_home:
        return (Path(data_home) / "iPhoto" / "maps" / "tiles" / "extension").resolve()
    return (Path.home() / ".local" / "share" / "iPhoto" / "maps" / "tiles" / "extension").resolve()


def _should_use_external_osmand_extension_root(package_root: Path) -> bool:
    if os.environ.get("APPIMAGE"):
        return True

    resolved_root = Path(package_root).resolve()
    writable_probe_root = resolved_root / "tiles"
    probe_target = writable_probe_root if writable_probe_root.exists() else resolved_root
    if not probe_target.exists():
        return False
    return not os.access(probe_target, os.W_OK)


def _default_osmand_search_roots(package_root: Path) -> tuple[Path, ...]:
    normalized_root = Path(package_root).resolve()
    bundled_root = normalized_root
    override_root = os.environ.get(ENV_OSMAND_EXTENSION_ROOT, "").strip()
    if override_root:
        external_root = Path(override_root).expanduser().resolve().parents[1]
    else:
        external_root = _default_external_osmand_extension_root().parents[1]
    if _should_use_external_osmand_extension_root(normalized_root):
        return _dedupe_paths((external_root, bundled_root))
    return _dedupe_paths((bundled_root, external_root))


def _dedupe_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return tuple(ordered)


def _collect_candidate_paths(
    search_roots: tuple[Path, ...],
    relative_paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    seen: set[Path] = set()
    candidates: list[Path] = []

    for root in search_roots:
        for relative_path in relative_paths:
            candidate = (root / relative_path).resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return tuple(candidates)


def _dedupe_candidates(candidates: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return tuple(deduped)


__all__ = [
    "DEFAULT_HELPER_RELATIVE_PATH",
    "DEFAULT_HELPER_RELATIVE_PATHS",
    "DEFAULT_OSMAND_PENDING_EXTENSION_SUFFIX",
    "DEFAULT_OSMAND_SEARCH_RELATIVE_PATH",
    "DEFAULT_OSMAND_EXTENSION_RELATIVE_ROOT",
    "DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC",
    "DEFAULT_NATIVE_WIDGET_RELATIVE_PATH",
    "DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS",
    "DEFAULT_OSMAND_RESOURCES_ROOT",
    "DEFAULT_OSMAND_STYLE_PATH",
    "LINUX_MAP_EXTENSION_DOWNLOAD_URL",
    "WINDOWS_MAP_EXTENSION_DOWNLOAD_URL",
    "ENV_OSMAND_HELPER",
    "ENV_OSMAND_NATIVE_WIDGET_LIBRARY",
    "ENV_OSMAND_EXTENSION_ROOT",
    "ENV_PREFER_OSMAND_NATIVE_WIDGET",
    "MapBackendMetadata",
    "MapSourceSpec",
    "apply_pending_osmand_extension_install",
    "default_osmand_extension_root",
    "default_osmand_tiles_root",
    "default_osmand_download_url",
    "default_pending_osmand_extension_root",
    "default_osmand_search_database",
    "has_installed_osmand_extension",
    "has_pending_osmand_extension_install",
    "has_usable_osmand_default",
    "has_usable_osmand_native_widget",
    "has_usable_osmand_search_extension",
    "prefer_osmand_native_widget",
    "resolve_osmand_helper_command",
    "resolve_osmand_native_widget_library",
    "supports_map_extension_download",
]
