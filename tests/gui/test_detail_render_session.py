from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_render_session import EditRenderState, PhotoRenderSessionHandle


def _surface(path: Path, *, level: int = 1024) -> tuple[AssetSourceIdentity, DecodedSurface]:
    identity = AssetSourceIdentity.create(
        path,
        size_bytes=123,
        source_mtime_ns=456,
        index_revision=7,
        width=1600,
        height=1200,
    )
    request = DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=identity,
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=level,
    )
    image = QImage(8, 6, QImage.Format.Format_RGBA8888)
    stats = ColorStats(saturation_mean=0.62, cast_magnitude=0.17)
    return identity, DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(1600, 1200),
        decoded_size=(8, 6),
        decode_level=level,
        backend="fake",
        color_stats=stats,
    )


def test_edit_render_state_is_immutable_and_uses_shared_stats(tmp_path: Path) -> None:
    _identity, surface = _surface(tmp_path / "photo.jpg")
    raw = {"Exposure": 0.4}
    state = EditRenderState.create(
        raw,
        color_stats=surface.color_stats,
        revision=("index", 7),
    )
    raw["Exposure"] = -0.8

    assert state.raw_adjustments["Exposure"] == 0.4
    assert state.color_stats is surface.color_stats
    with pytest.raises(TypeError):
        state.raw_adjustments["Exposure"] = 0.0  # type: ignore[index]


def test_session_updates_shader_state_without_changing_texture_key(tmp_path: Path) -> None:
    identity, surface = _surface(tmp_path / "photo.jpg")
    baseline = EditRenderState.create(
        {"Exposure": 0.1},
        color_stats=surface.color_stats,
        revision=("index", identity.index_revision),
    )
    handle = PhotoRenderSessionHandle(
        session_id=1,
        asset_id="asset-1",
        source_identity=identity,
        current_surface=surface,
        edit_state=baseline,
        baseline_state=baseline,
    )
    texture_key = handle.current_texture_key

    updated = handle.next_state({"Exposure": 0.8})

    assert updated.revision[0] == "session"
    assert handle.current_texture_key == texture_key
    assert handle.restore_baseline() is baseline
    assert handle.current_texture_key == texture_key


def test_session_lod_replacement_preserves_edit_state(tmp_path: Path) -> None:
    identity, first = _surface(tmp_path / "photo.jpg", level=1024)
    _identity, second = _surface(tmp_path / "photo.jpg", level=2048)
    state = EditRenderState.create(
        {"Crop_W": 0.5},
        color_stats=first.color_stats,
        revision=("session", 8),
    )
    handle = PhotoRenderSessionHandle(
        session_id=1,
        asset_id="asset-1",
        source_identity=identity,
        current_surface=first,
        edit_state=state,
        baseline_state=state,
    )

    handle.replace_surface(second)

    assert handle.current_texture_key == second.decode_key
    assert handle.available_lods == {1024, 2048}
    assert handle.edit_state is state
    assert handle.activate_surface(first.decode_key)
    assert handle.current_texture_key == first.decode_key


def test_session_retains_lod_without_committing_current_surface(tmp_path: Path) -> None:
    identity, first = _surface(tmp_path / "photo.jpg", level=1024)
    _identity, second = _surface(tmp_path / "photo.jpg", level=2048)
    state = EditRenderState.create(
        {},
        color_stats=first.color_stats,
        revision=("index", identity.index_revision),
    )
    handle = PhotoRenderSessionHandle(
        session_id=1,
        asset_id="asset-1",
        source_identity=identity,
        current_surface=first,
        edit_state=state,
        baseline_state=state,
    )

    handle.retain_surface(second)

    assert handle.current_texture_key == first.decode_key
    assert handle.surface_for_key(second.decode_key) is second
    assert handle.activate_surface(second.decode_key)
    assert handle.current_texture_key == second.decode_key
