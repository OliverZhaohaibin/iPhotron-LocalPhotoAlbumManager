"""Tests for Vignette sidecar persistence."""

from __future__ import annotations

import pytest
from pathlib import Path

from iPhoto.io.sidecar import (
    load_adjustments,
    save_adjustments,
)


def test_vignette_sidecar_round_trip(tmp_path: Path):
    """Vignette values survive a save/load round trip through the .ipo sidecar."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Vignette_Enabled": True,
        "Vignette_Strength": 0.75,
        "Vignette_Radius": 0.40,
        "Vignette_Softness": 0.60,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["Vignette_Enabled"] is True
    assert loaded["Vignette_Strength"] == pytest.approx(0.75, abs=0.01)
    assert loaded["Vignette_Radius"] == pytest.approx(0.40, abs=0.01)
    assert loaded["Vignette_Softness"] == pytest.approx(0.60, abs=0.01)


def test_vignette_sidecar_disabled_round_trip(tmp_path: Path):
    """Vignette disabled+zero state omits the node; defaults are returned."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Vignette_Enabled": False,
        "Vignette_Strength": 0.0,
        "Vignette_Radius": 0.50,
        "Vignette_Softness": 0.0,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    # When disabled with zero strength the node is omitted; absent keys default.
    assert loaded.get("Vignette_Enabled", False) is False
    assert loaded.get("Vignette_Strength", 0.0) == pytest.approx(0.0, abs=0.01)


def test_vignette_sidecar_clamped_round_trip(tmp_path: Path):
    """Vignette values are clamped to [0.0, 1.0] on load."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Vignette_Enabled": True,
        "Vignette_Strength": 0.85,
        "Vignette_Radius": 0.30,
        "Vignette_Softness": 0.90,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["Vignette_Enabled"] is True
    assert 0.0 <= loaded["Vignette_Strength"] <= 1.0
    assert 0.0 <= loaded["Vignette_Radius"] <= 1.0
    assert 0.0 <= loaded["Vignette_Softness"] <= 1.0
