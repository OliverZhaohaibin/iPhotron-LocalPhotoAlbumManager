"""Main People dashboard widget."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from iPhoto.people.repository import PeopleGroupSummary, PersonSummary
from iPhoto.people.service import PeopleService

from .flow_layout import FlowLayout
from .people_dashboard_board import PeopleBoard
from .people_dashboard_cards import GroupCard, PeopleCard
from .people_dashboard_dialogs import GroupPeopleDialog, MergeConfirmDialog
from .people_dashboard_shared import MENU_STYLE, _widget_uses_dark_theme


class PeopleDashboardWidget(QWidget):
    clusterActivated = Signal(str)
    groupActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PeopleService()
        self._status_message: str | None = None
        self._summaries: list[PersonSummary] = []
        self._groups: list[PeopleGroupSummary] = []
        self._cards: dict[str, PeopleCard] = {}
        self._group_cards: dict[str, GroupCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._title = QLabel("People")
        self._title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 700;")

        self._refresh_button = QToolButton()
        self._refresh_button.setText("Refresh")
        self._refresh_button.setAutoRaise(True)
        self._refresh_button.setStyleSheet("""
            QToolButton {
                border: none;
                color: #356CB4;
                font-size: 15px;
                font-weight: 600;
                padding: 6px 10px;
            }
            """)
        self._refresh_button.clicked.connect(self.reload)

        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setStyleSheet("color: #63739A; font-size: 13px;")
        root.addWidget(self._message)

        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet("""
            QLabel {
                padding: 32px;
                border: 1px dashed rgba(16, 24, 40, 0.14);
                border-radius: 24px;
                color: #667085;
                background: rgba(255, 255, 255, 0.72);
            }
            """)
        root.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("PeopleScrollArea")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("#PeopleScrollArea { background: transparent; border: none; }")
        self._scroll.hide()
        root.addWidget(self._scroll, 1)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(22)

        self._groups_section = QWidget()
        self._groups_section.setStyleSheet("background: transparent;")
        groups_layout = QVBoxLayout(self._groups_section)
        groups_layout.setContentsMargins(0, 0, 0, 0)
        groups_layout.setSpacing(10)

        self._groups_title = QLabel("Groups")
        self._groups_title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 800;")
        groups_layout.addWidget(self._groups_title)

        self._groups_host = QWidget()
        self._groups_host.setStyleSheet("background: transparent;")
        self._groups_layout = FlowLayout(self._groups_host, margin=0, h_spacing=18, v_spacing=18)
        groups_layout.addWidget(self._groups_host)
        self._content_layout.addWidget(self._groups_section)

        self._people_title = QLabel("People & Pets")
        self._people_title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 800;")
        self._content_layout.addWidget(self._people_title)

        self._board = PeopleBoard()
        self._board.mergeRequested.connect(self._merge_cluster_pair)
        self._content_layout.addWidget(self._board)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)

    def set_library_root(self, library_root: Path | None) -> None:
        self._service.set_library_root(library_root)
        self.reload()

    def build_cluster_query(self, person_id: str):
        return self._service.build_cluster_query(person_id)

    def build_group_query(self, group_id: str):
        return self._service.build_group_query(group_id)

    def set_status_message(self, message: str | None) -> None:
        self._status_message = message or None
        self.reload()

    def reload(self) -> None:
        self._clear_group_cards()
        self._clear_cards()
        if not self._service.is_bound():
            self._message.setText("Bind a Basic Library to see People clusters.")
            self._empty.setText("People appears here after a library is bound and scanned.")
            self._empty.show()
            self._scroll.hide()
            return

        self._summaries = self._service.list_clusters()
        self._groups = self._service.list_groups()
        counts = self._service.face_status_counts()
        pending = counts.get("pending", 0) + counts.get("retry", 0)

        if self._summaries:
            self._message.setText(
                "Click a cluster or group card to open matching assets, or drag cards close together to merge clusters."
            )
            self._empty.hide()
            self._scroll.show()
            self._populate_groups()
            self._populate_cards()
            return

        if self._status_message:
            body = self._status_message
        elif pending > 0:
            body = "Scanning faces in the background. This page will fill in as clusters are ready."
        else:
            body = "No People clusters yet. Run a scan to build face groups."
        self._message.setText(body)
        self._empty.setText(body)
        self._empty.show()
        self._scroll.hide()

    def _populate_groups(self) -> None:
        self._clear_group_cards()
        if not self._groups:
            self._groups_section.hide()
            return

        self._groups_section.show()
        for index, summary in enumerate(self._groups):
            card = GroupCard(summary=summary, seed_index=index, parent=self._groups_host)
            card.activated.connect(self.groupActivated.emit)
            self._group_cards[summary.group_id] = card
            self._groups_layout.addWidget(card)
            card.show()
        self._groups_host.updateGeometry()

    def _populate_cards(self) -> None:
        cards: list[PeopleCard] = []
        for index, summary in enumerate(self._summaries):
            card = PeopleCard(
                board=self._board,
                summary=summary,
                seed_index=index,
            )
            card.activated.connect(self.clusterActivated.emit)
            card.menuRequested.connect(self._show_card_menu)
            self._cards[summary.person_id] = card
            cards.append(card)
        self._board.set_cards(cards)

    def _clear_cards(self) -> None:
        self._board.clear_cards()
        self._cards.clear()

    def _clear_group_cards(self) -> None:
        while self._groups_layout.count():
            item = self._groups_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._group_cards.clear()

    def _summary_for_person(self, person_id: str) -> PersonSummary | None:
        return next((item for item in self._summaries if item.person_id == person_id), None)

    def _show_card_menu(self, person_id: str, global_pos) -> None:
        summary = self._summary_for_person(person_id)
        if summary is None:
            return

        menu = self._build_card_menu(summary)
        menu.exec(global_pos)

    def _build_card_menu(self, summary: PersonSummary) -> QMenu:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        rename_text = "Rename" if summary.name else "Name This Person"
        rename_action = QAction(rename_text, menu)
        new_group_action = QAction("New Group", menu)
        merge_action = QAction("Merge Into...", menu)
        merge_action.setEnabled(len(self._summaries) > 1)
        rename_action.triggered.connect(lambda: self._rename_person(summary))
        new_group_action.triggered.connect(lambda: self._open_group_dialog(summary.person_id))
        merge_action.triggered.connect(lambda: self._merge_person(summary))
        menu.addAction(rename_action)
        menu.addAction(new_group_action)
        menu.addSeparator()
        menu.addAction(merge_action)
        return menu

    def _rename_person(self, summary: PersonSummary) -> None:
        title = "Rename Person" if summary.name else "Name This Person"
        text, accepted = QInputDialog.getText(self, title, "Name:", text=summary.name or "")
        if not accepted:
            return
        self._service.rename_cluster(summary.person_id, text.strip() or None)
        self.reload()

    def _merge_person(self, summary: PersonSummary) -> None:
        choices = [
            (f"{target.name or 'Unnamed'} ({target.face_count} faces)", target.person_id)
            for target in self._summaries
            if target.person_id != summary.person_id
        ]
        if not choices:
            return

        labels = [label for label, _ in choices]
        selected, accepted = QInputDialog.getItem(
            self,
            "Merge Person",
            "Merge into:",
            labels,
            editable=False,
        )
        if not accepted:
            return
        selected_id = next((person_id for label, person_id in choices if label == selected), None)
        if selected_id is None:
            return
        self._confirm_merge(summary.person_id, selected_id)

    def _open_group_dialog(self, initial_person_id: str) -> None:
        if len(self._summaries) < 2:
            return
        dialog = GroupPeopleDialog(
            self._summaries,
            initial_selected_ids=[initial_person_id],
            dark_mode=self._uses_dark_theme(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        group = self._service.create_group(dialog.selected_person_ids())
        if group is not None:
            self.reload()

    def _merge_cluster_pair(self, source_person_id: str, target_person_id: str) -> None:
        self._confirm_merge(source_person_id, target_person_id)

    def _confirm_merge(self, source_person_id: str, target_person_id: str) -> bool:
        if source_person_id == target_person_id:
            return False

        source = self._summary_for_person(source_person_id)
        target = self._summary_for_person(target_person_id)
        if source is None or target is None:
            return False

        if not MergeConfirmDialog.confirm(2, self):
            return False

        merged = self._service.merge_clusters(source_person_id, target_person_id)
        if merged:
            self.reload()
        return merged

    def _uses_dark_theme(self) -> bool:
        window = self.window()
        coordinator = getattr(window, "coordinator", None)
        context = getattr(coordinator, "_context", None)
        theme_manager = getattr(context, "theme", None)
        if theme_manager is not None and hasattr(theme_manager, "get_effective_theme_mode"):
            return theme_manager.get_effective_theme_mode() == "dark"

        settings = getattr(context, "settings", None)
        if settings is not None and hasattr(settings, "get"):
            theme_setting = settings.get("ui.theme", "system")
            if theme_setting == "dark":
                return True
            if theme_setting == "light":
                return False

        app = QGuiApplication.instance()
        if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return True
        return _widget_uses_dark_theme(self)
