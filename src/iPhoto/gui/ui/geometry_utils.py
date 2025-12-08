"""Geometry utility functions for UI layout."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize


def calculate_center_crop(img_size: QSize, view_size: QSize) -> QRectF:
    """
    Calculate the source rectangle for an Aspect Fill (Center Crop) operation.

    Given an image size and a target view size, this function returns the
    rectangle within the source image that should be drawn to fill the view
    while preserving the aspect ratio and centering the content.

    Args:
        img_size (QSize): The size of the source image, in pixels.
        view_size (QSize): The size of the target view area, in pixels.

    Returns:
        QRectF: A rectangle in image coordinates representing the region to be drawn
            to fill the view with the correct aspect ratio, centered within the image.
    """
    img_w, img_h = img_size.width(), img_size.height()
    view_w, view_h = view_size.width(), view_size.height()

    if img_w <= 0 or img_h <= 0 or view_w <= 0 or view_h <= 0:
        return QRectF(0, 0, 0, 0)

    img_ratio = img_w / img_h
    view_ratio = view_w / view_h

    if img_ratio > view_ratio:
        # Image is wider relative to height than the view: Crop horizontal sides
        new_w = img_h * view_ratio
        offset_x = (img_w - new_w) / 2.0
        return QRectF(offset_x, 0.0, new_w, float(img_h))
    else:
        # Image is taller or equal ratio: Crop top/bottom
        new_h = img_w / view_ratio
        offset_y = (img_h - new_h) / 2.0
        return QRectF(0.0, offset_y, float(img_w), new_h)
