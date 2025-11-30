
import pytest
from iPhotos.src.iPhoto.io.sidecar import resolve_render_adjustments
from iPhotos.src.iPhoto.core.color_resolver import ColorStats

def test_bw_render_adjustments_range() -> None:
    """
    Verify that B&W adjustments are correctly mapped to the renderer's expected range.

    Expectations:
    - BW_Intensity (stored [0, 1], default 0.5): Mapped to [-1, 1]. 0.5 -> 0.0.
    - BW_Neutrals (stored [-1, 1], default 0.0): Passed raw. -0.5 -> -0.5.
    - BW_Tone (stored [-1, 1], default 0.0): Passed raw. 0.0 -> 0.0.
    - BW_Grain (stored [0, 1], default 0.0): Passed raw (clamped). 0.5 -> 0.5.
    """

    # Input dictionary simulates values read from a sidecar file
    raw = {
        "BW_Enabled": True,
        "BW_Intensity": 0.5,   # Neutral in [0, 1] scheme
        "BW_Neutrals": -0.5,   # Negative value in [-1, 1] scheme
        "BW_Tone": 0.0,        # Neutral in [-1, 1] scheme
        "BW_Grain": 0.5,       # Mid grain
    }

    resolved = resolve_render_adjustments(raw, color_stats=ColorStats())

    # BWIntensity: 0.5 -> 0.0
    assert resolved["BWIntensity"] == pytest.approx(0.0, abs=1e-6), \
        f"BWIntensity 0.5 should map to 0.0, got {resolved['BWIntensity']}"

    # BWNeutrals: -0.5 -> -0.5
    assert resolved["BWNeutrals"] == pytest.approx(-0.5, abs=1e-6), \
        f"BWNeutrals -0.5 should remain -0.5, got {resolved['BWNeutrals']}"

    # BWTone: 0.0 -> 0.0
    assert resolved["BWTone"] == pytest.approx(0.0, abs=1e-6)

    # BWGrain: 0.5 -> 0.5
    assert resolved["BWGrain"] == pytest.approx(0.5, abs=1e-6)

def test_bw_intensity_expansion() -> None:
    """Verify BWIntensity expansion from [0, 1] to [-1, 1]."""
    # 0.0 -> -1.0
    raw_soft = {"BW_Enabled": True, "BW_Intensity": 0.0}
    res_soft = resolve_render_adjustments(raw_soft)
    assert res_soft["BWIntensity"] == pytest.approx(-1.0, abs=1e-6)

    # 1.0 -> 1.0
    raw_rich = {"BW_Enabled": True, "BW_Intensity": 1.0}
    res_rich = resolve_render_adjustments(raw_rich)
    assert res_rich["BWIntensity"] == pytest.approx(1.0, abs=1e-6)
