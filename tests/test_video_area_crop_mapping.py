from iPhoto.gui.ui.widgets.video_area import _VideoGeometry, _rotate_crop_to_display
from PySide6.QtCore import QSizeF


def test_rotate_crop_to_display_quadrants() -> None:
    crop = (88, 12, 66, 22)
    assert _rotate_crop_to_display(crop, 0) == (88, 12, 66, 22)
    assert _rotate_crop_to_display(crop, 90) == (22, 66, 88, 12)
    assert _rotate_crop_to_display(crop, 180) == (12, 88, 22, 66)
    assert _rotate_crop_to_display(crop, 270) == (66, 22, 12, 88)


def test_video_geometry_has_crop() -> None:
    g_no_crop = _VideoGeometry(display_size=QSizeF(100, 200), coded_size=QSizeF(100, 200), rotation=0)
    g_crop = _VideoGeometry(
        display_size=QSizeF(100, 200),
        coded_size=QSizeF(120, 220),
        rotation=90,
        crop_left=1,
    )
    assert not g_no_crop.has_crop
    assert g_crop.has_crop
