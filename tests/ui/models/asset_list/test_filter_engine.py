"""Unit tests for ModelFilterHandler."""
from __future__ import annotations

from src.iPhoto.gui.ui.models.asset_list.filter_engine import ModelFilterHandler


def test_filter_handler_basic_modes():
    """Test setting and getting filter modes."""
    handler = ModelFilterHandler()
    
    assert not handler.is_active()
    assert handler.get_mode() is None
    
    # Set videos filter
    changed = handler.set_mode("videos")
    assert changed
    assert handler.is_active()
    assert handler.get_mode() == "videos"
    
    # Setting same mode returns False
    changed = handler.set_mode("videos")
    assert not changed
    
    # Clear filter
    changed = handler.set_mode(None)
    assert changed
    assert not handler.is_active()


def test_filter_handler_matches_videos():
    """Test video filtering."""
    handler = ModelFilterHandler()
    handler.set_mode("videos")
    
    # Video by media_type
    assert handler.matches_filter({"media_type": 1})
    
    # Video by is_video flag
    assert handler.matches_filter({"is_video": True})
    
    # Non-video
    assert not handler.matches_filter({"media_type": 0})
    assert not handler.matches_filter({"is_video": False})


def test_filter_handler_matches_live():
    """Test live photo filtering."""
    handler = ModelFilterHandler()
    handler.set_mode("live")
    
    # Live by is_live flag
    assert handler.matches_filter({"is_live": True})
    
    # Live by live_partner_rel
    assert handler.matches_filter({"live_partner_rel": "companion.mov"})
    
    # Non-live
    assert not handler.matches_filter({"is_live": False})
    assert not handler.matches_filter({})


def test_filter_handler_matches_favorites():
    """Test favorite filtering."""
    handler = ModelFilterHandler()
    handler.set_mode("favorites")
    
    # Favorite by featured flag
    assert handler.matches_filter({"featured": True})
    
    # Favorite by is_favorite flag
    assert handler.matches_filter({"is_favorite": 1})
    
    # Non-favorite
    assert not handler.matches_filter({"featured": False})
    assert not handler.matches_filter({"is_favorite": 0})


def test_filter_handler_filter_rows():
    """Test filtering a list of rows."""
    handler = ModelFilterHandler()
    
    rows = [
        {"rel": "a.jpg", "media_type": 0},
        {"rel": "b.mov", "media_type": 1},
        {"rel": "c.jpg", "media_type": 0},
    ]
    
    # No filter
    filtered = handler.filter_rows(rows)
    assert len(filtered) == 3
    
    # Videos only
    handler.set_mode("videos")
    filtered = handler.filter_rows(rows)
    assert len(filtered) == 1
    assert filtered[0]["rel"] == "b.mov"


def test_filter_handler_get_filter_params():
    """Test getting filter parameters for DB queries."""
    handler = ModelFilterHandler()
    
    # No filter
    params = handler.get_filter_params()
    assert params is None
    
    # With filter
    handler.set_mode("favorites")
    params = handler.get_filter_params()
    assert params == {"filter_mode": "favorites"}


def test_filter_handler_validate_mode():
    """Test mode validation."""
    handler = ModelFilterHandler()
    
    assert handler.is_valid_mode(None)
    assert handler.is_valid_mode("")
    assert handler.is_valid_mode("videos")
    assert handler.is_valid_mode("VIDEOS")  # Case insensitive
    assert handler.is_valid_mode("live")
    assert handler.is_valid_mode("favorites")
    
    assert not handler.is_valid_mode("invalid")
    assert not handler.is_valid_mode("photos")
