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
DEFAULT_HELPER_RELATIVE_PATH = Path("tools") / "osmand_render_helper_native" / "dist" / "osmand_render_helper.exe"


@dataclass(frozen=True)
class MapBackendMetadata:
    """Describe the capabilities of a concrete map backend."""

    min_zoom: float
    max_zoom: float
    provides_place_labels: bool
    tile_kind: Literal["vector", "raster"]


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
        if (
            Path(osmand.data_path).exists()
            and Path(osmand.resources_root or "").exists()
            and Path(osmand.style_path or "").exists()
        ):
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


def has_usable_osmand_default(package_root: Path | None = None) -> bool:
    """Return ``True`` when the bundled OBF source and helper are both available."""

    root = package_root or _package_root()
    source = MapSourceSpec.osmand_default(root).resolved(root)
    return (
        Path(source.data_path).exists()
        and Path(source.resources_root or "").exists()
        and Path(source.style_path or "").exists()
        and bool(source.helper_command)
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
        Path("binaries") / "windows" / "msvc-amd64" / "amd64" / "osmand_render_helper.exe",
    )
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in search_roots:
        candidate = (root / DEFAULT_HELPER_RELATIVE_PATH).resolve()
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
    "DEFAULT_OSMAND_RESOURCES_ROOT",
    "DEFAULT_OSMAND_STYLE_PATH",
    "DEFAULT_HELPER_RELATIVE_PATH",
    "DEFAULT_OFFICIAL_OSMAND_ROOT",
    "ENV_OSMAND_HELPER",
    "MapBackendMetadata",
    "MapSourceSpec",
    "has_usable_osmand_default",
    "resolve_osmand_helper_command",
]
