import pytest

from iPhoto.core.light_resolver import resolve_light_vector
from iPhoto.core.color_resolver import ColorStats
from iPhoto.io.sidecar import resolve_render_adjustments


def test_resolve_render_adjustments_blends_master_and_overrides() -> None:
    raw = {
        "Light_Master": 0.25,
        "Light_Enabled": True,
        "Shadows": 0.15,
    }
    resolved = resolve_render_adjustments(raw, color_stats=ColorStats())
    expected = resolve_light_vector(0.25, {"Shadows": 0.15})
    for key, value in expected.items():
        assert pytest.approx(value, rel=1e-6) == resolved[key]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Saturation"]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Vibrance"]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Cast"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_R"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_G"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_B"]


def test_resolve_render_adjustments_skips_when_disabled() -> None:
    raw = {
        "Light_Master": 0.6,
        "Light_Enabled": False,
        "Exposure": 0.4,
    }
    resolved = resolve_render_adjustments(raw, color_stats=ColorStats())
    assert pytest.approx(0.0, rel=1e-6) == resolved["Saturation"]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Vibrance"]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Cast"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_R"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_G"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Color_Gain_B"]
    for key in resolve_light_vector(0.0, {}):
        assert key not in resolved or resolved[key] == 0.0


def test_resolve_render_adjustments_handles_missing_values() -> None:
    resolved = resolve_render_adjustments({}, color_stats=ColorStats())
    assert resolved == {}


def test_resolve_render_adjustments_sharpen_enabled_gating() -> None:
    """Sharpen_Enabled gates whether clamped Sharpen params appear in the resolved dict."""

    raw_enabled = {
        "Sharpen_Enabled": True,
        "Sharpen_Intensity": 0.8,
        "Sharpen_Edges": 0.3,
        "Sharpen_Falloff": 0.6,
    }
    resolved = resolve_render_adjustments(raw_enabled, color_stats=ColorStats())
    assert resolved["Sharpen_Enabled"] is True
    assert pytest.approx(0.8, rel=1e-6) == resolved["Sharpen_Intensity"]
    assert pytest.approx(0.3, rel=1e-6) == resolved["Sharpen_Edges"]
    assert pytest.approx(0.6, rel=1e-6) == resolved["Sharpen_Falloff"]

    # When disabled, the dedicated section does not emit clamped values.
    # Out-of-range values should NOT be clamped when section is disabled.
    raw_disabled = {
        "Sharpen_Enabled": False,
        "Sharpen_Intensity": 2.5,
        "Sharpen_Edges": -0.3,
        "Sharpen_Falloff": 1.5,
    }
    resolved_off = resolve_render_adjustments(raw_disabled, color_stats=ColorStats())
    assert resolved_off["Sharpen_Enabled"] is False
    # Values flow through the generic loop unclamped when disabled; the
    # dedicated section only applies clamping when the section is enabled.
    assert pytest.approx(2.5, rel=1e-6) == resolved_off.get("Sharpen_Intensity", 0.0)
    assert pytest.approx(-0.3, rel=1e-6) == resolved_off.get("Sharpen_Edges", 0.0)
    assert pytest.approx(1.5, rel=1e-6) == resolved_off.get("Sharpen_Falloff", 0.0)


def test_resolve_render_adjustments_sharpen_clamping() -> None:
    """Sharpen params are clamped to [0, 1]."""

    raw = {
        "Sharpen_Enabled": True,
        "Sharpen_Intensity": 2.5,
        "Sharpen_Edges": -0.3,
        "Sharpen_Falloff": 1.5,
    }
    resolved = resolve_render_adjustments(raw, color_stats=ColorStats())
    assert pytest.approx(1.0, rel=1e-6) == resolved["Sharpen_Intensity"]
    assert pytest.approx(0.0, rel=1e-6) == resolved["Sharpen_Edges"]
    assert pytest.approx(1.0, rel=1e-6) == resolved["Sharpen_Falloff"]


def test_resolve_light_vector_scales_delta_strength() -> None:
    """The fine-tuning overrides should inherit the 0.1 sensitivity factor."""

    result = resolve_light_vector(0.0, {"Shadows": 0.5})
    assert pytest.approx(0.05, rel=1e-6) == result["Shadows"]
