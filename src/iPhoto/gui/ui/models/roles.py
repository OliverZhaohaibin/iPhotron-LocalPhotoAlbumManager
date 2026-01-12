"""Role definitions shared by the album models."""

from __future__ import annotations

from enum import IntEnum
from typing import Dict

from PySide6.QtCore import Qt


class Roles(IntEnum):
    """Custom roles exposed to QML or widgets."""

    REL = Qt.UserRole + 1
    ABS = Qt.UserRole + 2
    ASSET_ID = Qt.UserRole + 3
    IS_IMAGE = Qt.UserRole + 4
    IS_VIDEO = Qt.UserRole + 5
    IS_LIVE = Qt.UserRole + 6
    LIVE_GROUP_ID = Qt.UserRole + 7
    SIZE = Qt.UserRole + 8
    DT = Qt.UserRole + 9
    FEATURED = Qt.UserRole + 10
    LIVE_MOTION_REL = Qt.UserRole + 11
    LIVE_MOTION_ABS = Qt.UserRole + 12
    IS_CURRENT = Qt.UserRole + 13
    IS_SPACER = Qt.UserRole + 14
    LOCATION = Qt.UserRole + 15
    INFO = Qt.UserRole + 16
    IS_PANO = Qt.UserRole + 17
    COMPOSITE = Qt.UserRole + 18
    DT_SORT = Qt.UserRole + 19
    MICRO_THUMBNAIL = Qt.UserRole + 20
    IS_SELECTED = Qt.UserRole + 21
    THUMBNAIL_REV = Qt.UserRole + 22


def role_names(base: Dict[int, bytes] | None = None) -> Dict[int, bytes]:
    """Return a mapping of Qt role numbers to byte names."""

    mapping: Dict[int, bytes] = {} if base is None else dict(base)
    mapping.update(
        {
            int(Roles.REL): b"rel",
            int(Roles.ABS): b"abs",
            int(Roles.ASSET_ID): b"assetId",
            int(Roles.IS_IMAGE): b"isImage",
            int(Roles.IS_VIDEO): b"isVideo",
            int(Roles.IS_LIVE): b"isLive",
            int(Roles.LIVE_GROUP_ID): b"liveGroupId",
            int(Roles.LIVE_MOTION_REL): b"liveMotion",
            int(Roles.LIVE_MOTION_ABS): b"liveMotionAbs",
            int(Roles.SIZE): b"size",
            int(Roles.DT): b"dt",
            int(Roles.FEATURED): b"featured",
            int(Roles.IS_CURRENT): b"isCurrent",
            int(Roles.IS_SPACER): b"isSpacer",
            int(Roles.LOCATION): b"location",
            int(Roles.INFO): b"info",
            int(Roles.IS_PANO): b"isPano",
            int(Roles.COMPOSITE): b"composite",
            int(Roles.DT_SORT): b"dtSort",
            int(Roles.MICRO_THUMBNAIL): b"microThumbnail",
            int(Roles.IS_SELECTED): b"isSelected",
            int(Roles.THUMBNAIL_REV): b"thumbnailRev",
        }
    )
    return mapping
