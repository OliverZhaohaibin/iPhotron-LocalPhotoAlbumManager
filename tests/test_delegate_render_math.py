
import sys
from pathlib import Path
import pytest
from PySide6.QtCore import QRectF, QSize

# Ensure src is in python path
sys.path.insert(0, str(Path.cwd() / "src"))

from iPhoto.gui.ui.geometry_utils import calculate_center_crop

def test_landscape_in_square():
    # 200x100 image in 100x100 view
    img = QSize(200, 100)
    view = QSize(100, 100)
    # Expected: Center 100x100 of the image
    rect = calculate_center_crop(img, view)

    assert rect.x() == 50.0
    assert rect.y() == 0.0
    assert rect.width() == 100.0
    assert rect.height() == 100.0

def test_portrait_in_square():
    # 100x200 image in 100x100 view
    img = QSize(100, 200)
    view = QSize(100, 100)
    # Expected: Center 100x100
    rect = calculate_center_crop(img, view)

    assert rect.x() == 0.0
    assert rect.y() == 50.0
    assert rect.width() == 100.0
    assert rect.height() == 100.0

def test_exact_match():
    img = QSize(150, 100)
    view = QSize(300, 200) # Same ratio 1.5
    rect = calculate_center_crop(img, view)

    assert rect.x() == 0.0
    assert rect.y() == 0.0
    assert rect.width() == 150.0
    assert rect.height() == 100.0

def test_floating_point_precision():
    img = QSize(300, 100)
    view = QSize(100, 100)
    rect = calculate_center_crop(img, view)

    assert rect.width() == 100.0
    assert rect.x() == 100.0 # (300-100)/2 = 100

    img = QSize(100, 100)
    view = QSize(200, 100)
    rect = calculate_center_crop(img, view)

    assert rect.width() == 100.0
    assert rect.height() == 50.0
    assert rect.y() == 25.0

def test_zero_division_guard():
    assert calculate_center_crop(QSize(0, 100), QSize(100, 100)) == QRectF(0, 0, 0, 0)
    assert calculate_center_crop(QSize(100, 100), QSize(0, 100)) == QRectF(0, 0, 0, 0)
