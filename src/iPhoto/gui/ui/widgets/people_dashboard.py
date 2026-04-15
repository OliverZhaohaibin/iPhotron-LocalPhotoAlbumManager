"""Compatibility exports for People dashboard widgets."""

from __future__ import annotations

from .people_dashboard_board import PeopleBoard
from .people_dashboard_cards import GroupCard, PeopleCard
from .people_dashboard_dialogs import GroupAvatarTile, GroupPeopleDialog, MergeConfirmDialog
from .people_dashboard_widget import PeopleDashboardWidget

__all__ = [
    "GroupAvatarTile",
    "GroupCard",
    "GroupPeopleDialog",
    "MergeConfirmDialog",
    "PeopleBoard",
    "PeopleCard",
    "PeopleDashboardWidget",
]
