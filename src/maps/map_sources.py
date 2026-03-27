"""Definitions for selecting and describing map data sources."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_OSMAND_RESOURCES_ROOT = Path(r"D:\python_code\maps_of_iPhoto\OsmAnd-resources")
DEFAULT_OSMAND_STYLE_PATH = DEFAULT_OSMAND_RESOURCES_ROOT / "rendering_styles" / "snowmobile.render.xml"
DEFAULT_OFFICIAL_OSMAND_ROOT = Path(r"D:\python_code\maps_of_iPhoto")
ENV_OSMAND_HELPER = "IPHOTO_OSMAND_RENDER_HELPER"
ENV_OSMAND_NATIVE_WIDGET_LIBRARY = "IPHOTO_OSMAND_NATIVE_WIDGET_LIBRARY"
DEFAULT_HELPER_RELATIVE_PATH = Path("tools") / "osmand_render_helper_native" / "dist" / "osmand_render_helper.exe"
DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC = Path("tools") / "osmand_render_helper_native" / "dist-msvc" / "osmand_native_widget.dll"
DEFAULT_NATIVE_WIDGET_RELATIVE_PATH = Path("tools") / "osmand_render_helper_native" / "dist" / "osmand_native_widget.dll"
DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW = Path("tools") / "osmand_render_helper_native" / "dist" / "libosmand_native_widget.dll"


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
        return cls(
            kind="osmand_obf",
            data_path=root / "tiles" / "World_basemap_2.obf",
            resources_root=DEFAULT_OSMAND_RESOURCES_ROOT,
            style_path=DEFAULT_OSMAND_STYLE_PATH,
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
    """Return the native Qt widget DLL path when it is available."""

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
    """Return ``True`` when the bundled OBF source and native widget DLL are available."""

    root = package_root or _package_root()
    return _has_osmand_data_assets(root) and resolve_osmand_native_widget_library(root) is not None


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


def _default_helper_candidates(package_root: Path) -> tuple[Path, ...]:
    normalized_root = Path(package_root).resolve()
    search_roots = (
        normalized_root,
        normalized_root.parent,
        normalized_root.parent.parent,
    )
    official_roots = (
        DEFAULT_OFFICIAL_OSMAND_ROOT.resolve(),
    )
    official_relatives = (
        Path("binaries") / "windows" / "gcc-amd64" / "Release" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "gcc-amd64" / "amd64" / "Release" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "gcc-amd64" / "amd64" / "RelWithDebInfo" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "msvc-amd64" / "Release" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "Release" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "RelWithDebInfo" / "osmand_render_helper.exe",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "osmand_render_helper.exe",
    )
    return _collect_candidate_paths(search_roots, official_roots, DEFAULT_HELPER_RELATIVE_PATH, official_relatives)


def _default_native_widget_candidates(package_root: Path) -> tuple[Path, ...]:
    normalized_root = Path(package_root).resolve()
    search_roots = (
        normalized_root,
        normalized_root.parent,
        normalized_root.parent.parent,
    )
    official_roots = (
        DEFAULT_OFFICIAL_OSMAND_ROOT.resolve(),
    )
    official_relatives = (
        Path("binaries") / "windows" / "msvc-amd64" / "Release" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "Release" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "RelWithDebInfo" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "gcc-amd64" / "Release" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "gcc-amd64" / "amd64" / "Release" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "gcc-amd64" / "amd64" / "RelWithDebInfo" / "osmand_native_widget.dll",
        Path("binaries") / "windows" / "gcc-amd64" / "Release" / "libosmand_native_widget.dll",
        Path("binaries") / "windows" / "gcc-amd64" / "amd64" / "Release" / "libosmand_native_widget.dll",
    )
    official_candidates = _collect_candidate_paths((), official_roots, DEFAULT_NATIVE_WIDGET_RELATIVE_PATH, official_relatives)
    local_candidates_msvc = _collect_candidate_paths(search_roots, (), DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC, ())
    local_candidates = _collect_candidate_paths(search_roots, (), DEFAULT_NATIVE_WIDGET_RELATIVE_PATH, ())
    local_candidates_mingw = _collect_candidate_paths(search_roots, (), DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MINGW, ())
    return official_candidates + local_candidates_msvc + local_candidates + local_candidates_mingw


def _collect_candidate_paths(
    search_roots: tuple[Path, ...],
    official_roots: tuple[Path, ...],
    local_relative_path: Path,
    official_relatives: tuple[Path, ...],
) -> tuple[Path, ...]:
    seen: set[Path] = set()
    candidates: list[Path] = []

    for root in search_roots:
        candidate = (root / local_relative_path).resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)

    for root in official_roots:
        for relative in official_relatives:
            candidate = (root / relative).resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return tuple(candidates)


__all__ = [
    "DEFAULT_HELPER_RELATIVE_PATH",
    "DEFAULT_NATIVE_WIDGET_RELATIVE_PATH_MSVC",
    "DEFAULT_NATIVE_WIDGET_RELATIVE_PATH",
    "DEFAULT_OFFICIAL_OSMAND_ROOT",
    "DEFAULT_OSMAND_RESOURCES_ROOT",
    "DEFAULT_OSMAND_STYLE_PATH",
    "ENV_OSMAND_HELPER",
    "ENV_OSMAND_NATIVE_WIDGET_LIBRARY",
    "MapBackendMetadata",
    "MapSourceSpec",
    "has_usable_osmand_default",
    "has_usable_osmand_native_widget",
    "resolve_osmand_helper_command",
    "resolve_osmand_native_widget_library",
]
