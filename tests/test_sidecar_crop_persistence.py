"""Regression tests covering crop persistence in ``.ipo`` XML sidecars."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from iPhotos.src.iPhoto.io import sidecar


def _write_xml(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_adjustments_reads_structured_crop_node(tmp_path) -> None:
    """The XML ``<crop>`` node should round-trip to centre-based adjustments."""

    asset = tmp_path / "sample.jpg"
    asset.write_bytes(b"")
    structured_xml = """<?xml version='1.0' encoding='UTF-8'?>
<iPhotoAdjustments version='1.0'>
  <crop>
    <x>0.1000</x>
    <y>0.2000</y>
    <w>0.5000</w>
    <h>0.4000</h>
  </crop>
</iPhotoAdjustments>
"""
    _write_xml(sidecar.sidecar_path_for_asset(asset), structured_xml)

    adjustments = sidecar.load_adjustments(asset)

    assert pytest.approx(0.35, rel=1e-6) == adjustments["Crop_CX"]
    assert pytest.approx(0.4, rel=1e-6) == adjustments["Crop_CY"]
    assert pytest.approx(0.5, rel=1e-6) == adjustments["Crop_W"]
    assert pytest.approx(0.4, rel=1e-6) == adjustments["Crop_H"]


def test_load_adjustments_falls_back_to_legacy_attributes(tmp_path) -> None:
    """Legacy ``<Crop cx=...>`` attributes must still be parsed correctly."""

    asset = tmp_path / "legacy.jpg"
    asset.write_bytes(b"")
    legacy_xml = """<?xml version='1.0' encoding='UTF-8'?>
<iPhotoAdjustments version='1.0'>
  <Crop cx='0.55' cy='0.45' w='0.8' h='0.6' />
</iPhotoAdjustments>
"""
    _write_xml(sidecar.sidecar_path_for_asset(asset), legacy_xml)

    adjustments = sidecar.load_adjustments(asset)

    assert pytest.approx(0.55, rel=1e-6) == adjustments["Crop_CX"]
    assert pytest.approx(0.45, rel=1e-6) == adjustments["Crop_CY"]
    assert pytest.approx(0.8, rel=1e-6) == adjustments["Crop_W"]
    assert pytest.approx(0.6, rel=1e-6) == adjustments["Crop_H"]


def test_save_adjustments_writes_structured_crop_and_preserves_nodes(tmp_path) -> None:
    """Saving adjustments should update ``<crop>`` without removing custom nodes."""

    asset = tmp_path / "preserve.jpg"
    asset.write_bytes(b"")
    existing_sidecar = tmp_path / "preserve.ipo"
    existing_sidecar.write_text(
        """<?xml version='1.0' encoding='UTF-8'?>
<iPhotoAdjustments version='0.9'>
  <uuid>abc-123</uuid>
  <note>keep me</note>
</iPhotoAdjustments>
""",
        encoding="utf-8",
    )

    adjustments = {
        "Light_Master": 0.2,
        "Light_Enabled": True,
        "Crop_CX": 0.6,
        "Crop_CY": 0.4,
        "Crop_W": 0.5,
        "Crop_H": 0.3,
    }
    sidecar.save_adjustments(asset, adjustments)

    tree = ET.parse(existing_sidecar)
    root = tree.getroot()

    uuid_node = root.find("uuid")
    assert uuid_node is not None
    assert uuid_node.text == "abc-123"

    crop_node = root.find("crop")
    assert crop_node is not None
    assert not crop_node.attrib  # Structured storage uses child nodes only
    x_node = crop_node.find("x")
    y_node = crop_node.find("y")
    assert x_node is not None and pytest.approx(0.35, rel=1e-6) == float(x_node.text)
    assert y_node is not None and pytest.approx(0.25, rel=1e-6) == float(y_node.text)


def test_perspective_values_roundtrip(tmp_path) -> None:
    """Perspective vertical and horizontal values should persist to the crop node."""

    asset = tmp_path / "perspective.jpg"
    asset.write_bytes(b"")
    
    # Save adjustments with perspective values
    adjustments = {
        "Light_Master": 0.0,
        "Light_Enabled": True,
        "Crop_CX": 0.5,
        "Crop_CY": 0.5,
        "Crop_W": 1.0,
        "Crop_H": 1.0,
        "Perspective_Vertical": 0.3,
        "Perspective_Horizontal": -0.2,
    }
    sidecar.save_adjustments(asset, adjustments)
    
    # Load the adjustments back
    loaded = sidecar.load_adjustments(asset)
    
    # Verify perspective values are preserved
    assert pytest.approx(0.3, rel=1e-6) == loaded["Perspective_Vertical"]
    assert pytest.approx(-0.2, rel=1e-6) == loaded["Perspective_Horizontal"]
    
    # Verify the XML structure
    sidecar_path = sidecar.sidecar_path_for_asset(asset)
    tree = ET.parse(sidecar_path)
    root = tree.getroot()
    crop_node = root.find("crop")
    assert crop_node is not None
    
    vertical_node = crop_node.find("vertical")
    horizontal_node = crop_node.find("horizontal")
    assert vertical_node is not None and pytest.approx(0.3, rel=1e-6) == float(vertical_node.text)
    assert horizontal_node is not None and pytest.approx(-0.2, rel=1e-6) == float(horizontal_node.text)

