"""Tests for White Balance sidecar persistence and render adjustment resolution."""

from __future__ import annotations

import pytest
from pathlib import Path

from src.iPhoto.io.sidecar import (
    load_adjustments,
    save_adjustments,
    resolve_render_adjustments,
)


def test_wb_render_adjustments_range():
    """WB adjustments are correctly resolved for the rendering pipeline."""

    adjustments = {
        "WB_Enabled": True,
        "WB_Warmth": 0.5,
        "WB_Temperature": -0.3,
        "WB_Tint": 0.7,
    }
    resolved = resolve_render_adjustments(adjustments)
    assert resolved["WB_Enabled"] is True
    assert resolved["WBWarmth"] == pytest.approx(0.5)
    assert resolved["WBTemperature"] == pytest.approx(-0.3)
    assert resolved["WBTint"] == pytest.approx(0.7)


def test_wb_render_adjustments_disabled():
    """When WB is disabled, all shader values should be zero."""

    adjustments = {
        "WB_Enabled": False,
        "WB_Warmth": 0.8,
        "WB_Temperature": 0.4,
        "WB_Tint": -0.6,
    }
    resolved = resolve_render_adjustments(adjustments)
    assert resolved["WB_Enabled"] is False
    assert resolved["WBWarmth"] == pytest.approx(0.0)
    assert resolved["WBTemperature"] == pytest.approx(0.0)
    assert resolved["WBTint"] == pytest.approx(0.0)


def test_wb_render_adjustments_defaults():
    """Missing WB keys should default to zero / disabled."""

    adjustments = {"Light_Enabled": True}
    resolved = resolve_render_adjustments(adjustments)
    assert resolved.get("WB_Enabled") is False
    assert resolved.get("WBWarmth") == pytest.approx(0.0)
    assert resolved.get("WBTemperature") == pytest.approx(0.0)
    assert resolved.get("WBTint") == pytest.approx(0.0)


def test_wb_sidecar_round_trip(tmp_path: Path):
    """WB values survive a save/load round trip through the .ipo sidecar."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "WB_Enabled": True,
        "WB_Warmth": 0.42,
        "WB_Temperature": -0.15,
        "WB_Tint": 0.88,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["WB_Enabled"] is True
    assert loaded["WB_Warmth"] == pytest.approx(0.42, abs=0.01)
    assert loaded["WB_Temperature"] == pytest.approx(-0.15, abs=0.01)
    assert loaded["WB_Tint"] == pytest.approx(0.88, abs=0.01)


def test_wb_sidecar_disabled_round_trip(tmp_path: Path):
    """WB disabled state round trips correctly."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "WB_Enabled": False,
        "WB_Warmth": 0.0,
        "WB_Temperature": 0.0,
        "WB_Tint": 0.0,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["WB_Enabled"] is False
    assert loaded["WB_Warmth"] == pytest.approx(0.0, abs=0.01)
    assert loaded["WB_Temperature"] == pytest.approx(0.0, abs=0.01)
    assert loaded["WB_Tint"] == pytest.approx(0.0, abs=0.01)
