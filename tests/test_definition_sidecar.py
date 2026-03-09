"""Tests for Definition sidecar persistence."""

from __future__ import annotations

import pytest
from pathlib import Path

from iPhoto.io.sidecar import (
    load_adjustments,
    save_adjustments,
)


def test_definition_sidecar_round_trip(tmp_path: Path):
    """Definition values survive a save/load round trip through the .ipo sidecar."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Definition_Enabled": True,
        "Definition_Value": 0.65,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    assert loaded["Definition_Enabled"] is True
    assert loaded["Definition_Value"] == pytest.approx(0.65, abs=0.01)


def test_definition_sidecar_disabled_round_trip(tmp_path: Path):
    """Definition disabled+zero state omits the node; defaults are returned."""

    asset = tmp_path / "photo.jpg"
    asset.touch()

    original = {
        "Definition_Enabled": False,
        "Definition_Value": 0.0,
    }
    save_adjustments(asset, original)
    loaded = load_adjustments(asset)

    # When disabled with zero value the node is omitted; absent keys default to off/zero.
    assert loaded.get("Definition_Enabled", False) is False
    assert loaded.get("Definition_Value", 0.0) == pytest.approx(0.0, abs=0.01)
