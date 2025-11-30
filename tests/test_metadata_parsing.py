from iPhotos.src.iPhoto.io.metadata import _coerce_fractional
import pytest

def test_coerce_fractional_duplicate_sum():
    """Test that _coerce_fractional does not sum duplicate values in the string."""
    # Scenario: Metadata contains both fractional and decimal representation
    # e.g. "1/125s (0.008)" which some tools might output
    value = "1/125s (0.008)"

    # Expected: 0.008 (1/125)
    # Before fix (bug): 0.008 + 0.008 = 0.016

    result = _coerce_fractional(value)
    assert result == pytest.approx(0.008, rel=1e-3)

def test_coerce_fractional_standard_cases():
    """Ensure standard cases still work correctly."""
    assert _coerce_fractional("1/30") == pytest.approx(1/30)
    assert _coerce_fractional("30") == 30.0
    assert _coerce_fractional("2.8") == 2.8
    assert _coerce_fractional("0") == 0.0
    assert _coerce_fractional(None) is None
    assert _coerce_fractional("") is None

def test_coerce_fractional_negative():
    """Ensure negative values are handled."""
    assert _coerce_fractional("-1") == -1.0
    assert _coerce_fractional("-0.5") == -0.5
