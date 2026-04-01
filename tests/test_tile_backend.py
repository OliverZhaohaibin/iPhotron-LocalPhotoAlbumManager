from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from maps.map_sources import MapBackendMetadata, MapSourceSpec
from maps.tile_backend import (
    FallbackTileBackend,
    LegacyVectorBackend,
    OsmAndRasterBackend,
    TileBackendUnavailableError,
)


@dataclass
class _FakeBackend:
    metadata: MapBackendMetadata
    probe_error: Exception | None = None
    load_error: Exception | None = None
    tile: object | None = None

    def __post_init__(self) -> None:
        self.probe_calls = 0
        self.load_calls = 0
        self.cleared = False
        self.shutdown_called = False
        self.device_scales: list[float] = []

    def probe(self) -> MapBackendMetadata:
        self.probe_calls += 1
        if self.probe_error is not None:
            raise self.probe_error
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> object | None:
        self.load_calls += 1
        if self.load_error is not None:
            raise self.load_error
        return self.tile

    def clear_cache(self) -> None:
        self.cleared = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def set_device_scale(self, scale: float) -> None:
        self.device_scales.append(scale)


def test_fallback_backend_uses_legacy_metadata_when_primary_probe_fails() -> None:
    fallback_metadata = MapBackendMetadata(0.0, 6.0, False, "vector")
    primary = _FakeBackend(
        metadata=MapBackendMetadata(2.0, 19.0, True, "raster"),
        probe_error=TileBackendUnavailableError("helper missing"),
    )
    fallback = _FakeBackend(metadata=fallback_metadata, tile={"legacy": True})

    backend = FallbackTileBackend(primary, fallback)

    assert backend.probe() == fallback_metadata
    assert backend.load_tile(1, 2, 3) == {"legacy": True}
    assert primary.load_calls == 0
    assert fallback.load_calls == 1


def test_fallback_backend_disables_primary_after_runtime_unavailable() -> None:
    primary = _FakeBackend(
        metadata=MapBackendMetadata(2.0, 18.0, True, "raster"),
        load_error=TileBackendUnavailableError("helper crashed"),
    )
    fallback = _FakeBackend(
        metadata=MapBackendMetadata(0.0, 6.0, False, "vector"),
        tile={"legacy": True},
    )

    backend = FallbackTileBackend(primary, fallback)

    first_tile = backend.load_tile(4, 5, 6)
    second_tile = backend.load_tile(4, 5, 7)

    assert first_tile == {"legacy": True}
    assert second_tile == {"legacy": True}
    assert primary.load_calls == 1
    assert primary.shutdown_called is True
    assert fallback.load_calls == 2
    assert backend.probe() == fallback.metadata


def test_legacy_vector_backend_restores_previous_interactive_zoom_range(tmp_path) -> None:
    tile_root = tmp_path / "tiles"
    tile_root.mkdir()
    (tile_root / "tiles.json").write_text(
        json.dumps({"minzoom": 0, "maxzoom": 6}),
        encoding="utf-8",
    )

    backend = LegacyVectorBackend(
        MapSourceSpec(
            kind="legacy_pbf",
            data_path=tile_root,
            style_path=tmp_path / "style.json",
        )
    )

    metadata = backend.probe()

    assert metadata.min_zoom == 2.0
    assert metadata.max_zoom == 8.5
    assert metadata.fetch_max_zoom == 6


def test_osmand_raster_backend_retries_once_after_runtime_unavailable(tmp_path, monkeypatch) -> None:
    source = MapSourceSpec(
        kind="osmand_obf",
        data_path=tmp_path / "world.obf",
        resources_root=tmp_path,
        style_path=tmp_path / "style.xml",
        helper_command=("helper.exe",),
    )
    backend = OsmAndRasterBackend(source)
    cache_path = tmp_path / "cache" / "tile.png"
    attempts: list[int] = []

    monkeypatch.setattr(backend, "_validate_paths", lambda: None)
    monkeypatch.setattr(backend, "_cache_file_path", lambda z, x, y: cache_path)
    monkeypatch.setattr(backend, "_ensure_process", lambda: object())

    def _fake_communicate(process, payload, *, timeout_ms=5000):
        del process, payload, timeout_ms
        attempts.append(1)
        if len(attempts) == 1:
            raise TileBackendUnavailableError("helper timeout")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"png")
        return {"status": "ok"}

    monkeypatch.setattr(backend, "_communicate", _fake_communicate)
    monkeypatch.setattr(backend, "_load_cached_tile", lambda path: ("tile", Path(path)))

    shutdown_calls: list[int] = []
    monkeypatch.setattr(backend, "shutdown", lambda: shutdown_calls.append(1))

    tile = backend.load_tile(2, 0, 0)

    assert tile == ("tile", cache_path)
    assert len(attempts) == 2
    assert len(shutdown_calls) == 1
