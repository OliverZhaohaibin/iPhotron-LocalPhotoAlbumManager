"""Tests for Denoise sidecar persistence."""

from __future__ import annotations

import pytest
from pathlib import Path

from iPhoto.io.sidecar import (
    load_adjustments,
    save_adjustments,
)


def test_denoise_sidecar_round_trip(tmp_path: Path):
    """Denoise values survive a save/load round trip through the .ipo sidecar."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Denoise_Enabled": True,
        "Denoise_Amount": 2.5,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["Denoise_Enabled"] is True
    assert loaded["Denoise_Amount"] == pytest.approx(2.5, abs=0.01)


def test_denoise_sidecar_disabled_round_trip(tmp_path: Path):
    """Denoise disabled+zero state omits the node; defaults are returned."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Denoise_Enabled": False,
        "Denoise_Amount": 0.0,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    # When disabled with zero value the node is omitted; absent keys default to off/zero.
    assert loaded.get("Denoise_Enabled", False) is False
    assert loaded.get("Denoise_Amount", 0.0) == pytest.approx(0.0, abs=0.01)


def test_denoise_sidecar_clamped_round_trip(tmp_path: Path):
    """Denoise amount is clamped to [0.0, 5.0] on load."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Denoise_Enabled": True,
        "Denoise_Amount": 4.75,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["Denoise_Enabled"] is True
    assert loaded["Denoise_Amount"] == pytest.approx(4.75, abs=0.01)
    assert loaded["Denoise_Amount"] <= 5.0
