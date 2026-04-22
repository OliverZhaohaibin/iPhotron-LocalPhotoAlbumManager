"""Main People dashboard widget."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
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

from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.people.repository import PeopleGroupSummary, PersonSummary
from iPhoto.people.service import PeopleService

from . import dialogs
from .flow_layout import FlowLayout
from .people_dashboard_board import PeopleBoard
from .people_dashboard_cards import GroupCard, PeopleCard
from .people_dashboard_dialogs import GroupPeopleDialog, MergeConfirmDialog
from .people_dashboard_shared import (
    CANVAS_MARGIN,
    MENU_STYLE,
    _widget_uses_dark_theme,
    configure_people_cover_cache,
)


class _PeopleDashboardLoaderSignals(QObject):
    loaded = Signal(int, int, bool, list, list, int, object)


class _PeopleDashboardLoaderWorker(QRunnable):
    def __init__(
        self,
        *,
        generation: int,
        index_version: int,
        library_root: Path | None,
        status_message: str | None,
        signals: _PeopleDashboardLoaderSignals,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._index_version = index_version
        self._library_root = library_root
        self._status_message = status_message
        self._signals = signals

    def run(self) -> None:
        if self._library_root is None:
            self._signals.loaded.emit(
                self._generation,
                self._index_version,
                False,
                [],
                [],
                0,
                self._status_message,
            )
            return
        service = PeopleService(self._library_root)
        summaries, groups, pending = service.load_dashboard()
        self._signals.loaded.emit(
            self._generation,
            self._index_version,
            True,
            summaries,
            groups,
            pending,
            self._status_message,
        )


class PeopleDashboardWidget(QWidget):
    clusterActivated = Signal(str)  # noqa: N815
    groupActivated = Signal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PeopleService()
        self._pinned_service: PinnedItemsService | None = None
        self._status_message: str | None = None
        self._summaries: list[PersonSummary] = []
        self._groups: list[PeopleGroupSummary] = []
        self._cards: dict[str, PeopleCard] = {}
        self._group_cards: dict[str, GroupCard] = {}
        self._load_generation = 0
        self._loading = False
        self._index_version = 0
        self._loaded_index_version = -1
        self._pending_index_refresh = False
        self._current_library_root: Path | None = None
        self._load_signals = _PeopleDashboardLoaderSignals()
        self._load_signals.loaded.connect(self._on_load_completed)
        self._load_pool = QThreadPool.globalInstance()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._flush_pending_refresh)

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
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_activity)
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
        self._groups_layout = FlowLayout(
            self._groups_host,
            margin=CANVAS_MARGIN,
            h_spacing=18,
            v_spacing=18,
        )
        self._groups_host.setLayout(self._groups_layout)
        groups_layout.addWidget(self._groups_host)
        self._content_layout.addWidget(self._groups_section)

        self._people_title = QLabel("People & Pets")
        self._people_title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 800;")
        self._content_layout.addWidget(self._people_title)

        self._board = PeopleBoard()
        self._board.mergeRequested.connect(self._merge_cluster_pair)
        self._board.orderChanged.connect(self._persist_cluster_order)
        self._content_layout.addWidget(self._board)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        self._apply_theme_styles()

    def set_library_root(self, library_root: Path | None) -> None:
        if self._current_library_root == library_root:
            return
        self._current_library_root = library_root
        self._service.set_library_root(library_root)
        configure_people_cover_cache(library_root)
        self.reload()

    def set_pinned_service(self, service: PinnedItemsService | None) -> None:
        self._pinned_service = service

    def build_cluster_query(self, person_id: str):
        return self._service.build_cluster_query(person_id)

    def build_group_query(self, group_id: str):
        return self._service.build_group_query(group_id)

    def set_status_message(self, message: str | None) -> None:
        self._status_message = message or None
        self._update_status_labels()

    def schedule_index_refresh(self) -> None:
        self._index_version += 1
        if not self.isVisible():
            self._pending_index_refresh = True
            return
        self._pending_index_refresh = True
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def reload(self, *, preserve_content: bool = False) -> None:
        self._load_generation += 1
        generation = self._load_generation
        index_version = self._index_version
        self._loading = True
        library_root = self._service.library_root()
        if library_root is None:
            self._loading = False
            self._loaded_index_version = index_version
            self._message.setText("Bind a Basic Library to see People clusters.")
            self._empty.setText("People appears here after a library is bound and scanned.")
            self._empty.show()
            self._scroll.hide()
            return

        if not preserve_content or (not self._summaries and not self._groups):
            self._message.setText("Loading People dashboard…")
            self._empty.setText("Loading People dashboard…")
            self._empty.show()
            self._scroll.hide()

        worker = _PeopleDashboardLoaderWorker(
            generation=generation,
            index_version=index_version,
            library_root=library_root,
            status_message=self._status_message,
            signals=self._load_signals,
        )
        self._load_pool.start(worker)

    def _on_load_completed(
        self,
        generation: int,
        index_version: int,
        is_bound: bool,
        summaries: list[PersonSummary],
        groups: list[PeopleGroupSummary],
        pending: int,
        status_message: str | None,
    ) -> None:
        if generation != self._load_generation:
            return
        self._loading = False
        if not is_bound:
            self._loaded_index_version = index_version
            self._message.setText("Bind a Basic Library to see People clusters.")
            self._empty.setText("People appears here after a library is bound and scanned.")
            self._empty.show()
            self._scroll.hide()
            return

        next_summaries = list(summaries)
        next_groups = list(groups)
        cards_changed = next_summaries != self._summaries or next_groups != self._groups
        self._summaries = next_summaries
        self._groups = next_groups
        self._loaded_index_version = index_version
        self._pending_index_refresh = False
        status_text = status_message if status_message else self._status_message

        if self._summaries:
            self._message.setText(
                "Click a cluster or group card to open matching assets, "
                "or drag cards close together to merge clusters."
            )
            self._empty.hide()
            self._scroll.show()
            if cards_changed:
                self._populate_groups()
                self._populate_cards()
            return

        if status_text:
            body = status_text
        elif pending > 0:
            body = "Scanning faces in the background. This page will fill in as clusters are ready."
        else:
            body = "No People clusters yet. Run a scan to build face groups."
        self._message.setText(body)
        self._empty.setText(body)
        self._empty.show()
        self._scroll.hide()
        self._clear_group_cards()
        self._clear_cards()

        if self._loaded_index_version < self._index_version:
            self._schedule_visible_refresh()

    def _populate_groups(self) -> None:
        self._clear_group_cards()
        if not self._groups:
            self._groups_section.hide()
            return

        self._groups_section.show()
        for index, summary in enumerate(self._groups):
            card = GroupCard(summary=summary, seed_index=index, parent=self._groups_host)
            card.activated.connect(self.groupActivated.emit)
            card.menuRequested.connect(self._show_group_menu)
            self._group_cards[summary.group_id] = card
            self._groups_layout.addWidget(card)
            card.load_cover_artwork()
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
        for card in cards:
            card.load_cover_artwork()

    def _on_scroll_activity(self) -> None:
        if self._pending_index_refresh and self.isVisible():
            self._schedule_visible_refresh()

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

    def _show_group_menu(self, group_id: str, global_pos) -> None:
        summary = self._group_summary_for_group(group_id)
        if summary is None:
            return

        menu = self._build_group_menu(summary)
        menu.exec(global_pos)

    def _build_card_menu(self, summary: PersonSummary) -> QMenu:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        rename_text = "Rename" if summary.name else "Name This Person"
        rename_action = QAction(rename_text, menu)
        new_group_action = QAction("New Group", menu)
        pin_action = QAction(
            "Unpin" if self._is_person_pinned(summary.person_id) else "Pin",
            menu,
        )
        merge_action = QAction("Merge Into...", menu)
        merge_action.setEnabled(len(self._summaries) > 1)
        rename_action.triggered.connect(lambda: self._rename_person(summary))
        new_group_action.triggered.connect(lambda: self._open_group_dialog(summary.person_id))
        pin_action.triggered.connect(lambda: self._toggle_person_pin(summary))
        pin_action.setEnabled(self._pin_actions_available())
        merge_action.triggered.connect(lambda: self._merge_person(summary))
        menu.addAction(rename_action)
        menu.addAction(new_group_action)
        menu.addAction(pin_action)
        menu.addSeparator()
        menu.addAction(merge_action)
        return menu

    def _build_group_menu(self, summary: PeopleGroupSummary) -> QMenu:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        pin_action = QAction(
            "Unpin" if self._is_group_pinned(summary.group_id) else "Pin",
            menu,
        )
        pin_action.setEnabled(self._pin_actions_available())
        pin_action.triggered.connect(lambda: self._toggle_group_pin(summary))
        menu.addAction(pin_action)
        return menu

    def _rename_person(self, summary: PersonSummary) -> None:
        title = "Rename Person" if summary.name else "Name This Person"
        text, accepted = QInputDialog.getText(self, title, "Name:", text=summary.name or "")
        if not accepted:
            return
        self._service.rename_cluster(summary.person_id, text.strip() or None)
        self.reload(preserve_content=bool(self._summaries))

    def _toggle_person_pin(self, summary: PersonSummary) -> None:
        if self._pinned_service is None:
            return
        library_root = self._service.library_root()
        if library_root is None:
            return
        if self._is_person_pinned(summary.person_id):
            self._pinned_service.unpin(
                kind="person",
                item_id=summary.person_id,
                library_root=library_root,
            )
            return

        block_reason = self._service.pin_block_reason(summary.person_id)
        if block_reason:
            dialogs.show_warning(self, block_reason)
            return

        label = str(summary.name or "").strip()
        renamed = False
        if not label:
            label = self._prompt_required_person_name(summary)
            if not label:
                return
            self._service.rename_cluster(summary.person_id, label)
            renamed = True

        self._pinned_service.pin_person(
            summary.person_id,
            label,
            library_root=library_root,
        )
        if renamed:
            self.reload(preserve_content=bool(self._summaries))

    def _toggle_group_pin(self, summary: PeopleGroupSummary) -> None:
        if self._pinned_service is None:
            return
        library_root = self._service.library_root()
        if library_root is None:
            return
        if self._is_group_pinned(summary.group_id):
            self._pinned_service.unpin(
                kind="group",
                item_id=summary.group_id,
                library_root=library_root,
            )
            return

        label = str(summary.name or "").strip()
        if not label:
            label = self._pinned_service.next_group_label(library_root)
        self._pinned_service.pin_group(
            summary.group_id,
            label,
            library_root=library_root,
        )

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
            self.reload(preserve_content=bool(self._summaries))

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
            self.reload(preserve_content=bool(self._summaries))
        return merged

    def _persist_cluster_order(self, ordered_person_ids: list[str]) -> None:
        current_ids = {summary.person_id for summary in self._summaries}
        filtered = [person_id for person_id in ordered_person_ids if person_id in current_ids]
        if len(filtered) != len(self._summaries):
            filtered.extend(
                summary.person_id
                for summary in self._summaries
                if summary.person_id not in set(filtered)
            )
        if filtered:
            self._service.set_cluster_order(filtered)

    def _group_summary_for_group(self, group_id: str) -> PeopleGroupSummary | None:
        return next((item for item in self._groups if item.group_id == group_id), None)

    def _is_person_pinned(self, person_id: str) -> bool:
        if self._pinned_service is None:
            return False
        return self._pinned_service.is_pinned(
            kind="person",
            item_id=person_id,
            library_root=self._service.library_root(),
        )

    def _is_group_pinned(self, group_id: str) -> bool:
        if self._pinned_service is None:
            return False
        return self._pinned_service.is_pinned(
            kind="group",
            item_id=group_id,
            library_root=self._service.library_root(),
        )

    def _pin_actions_available(self) -> bool:
        return self._pinned_service is not None and self._service.library_root() is not None

    def _prompt_required_person_name(self, summary: PersonSummary) -> str | None:
        title = "Name This Person"
        text, accepted = QInputDialog.getText(self, title, "Name:", text=summary.name or "")
        if not accepted:
            return None
        normalized = text.strip()
        if normalized:
            return normalized
        dialogs.show_warning(self, "A name is required before pinning this person.")
        return None

    def _update_status_labels(self) -> None:
        if self._loading:
            return
        if self._summaries:
            self._message.setText(
                "Click a cluster or group card to open matching assets, "
                "or drag cards close together to merge clusters."
            )
            return
        if self._status_message:
            self._message.setText(self._status_message)
            self._empty.setText(self._status_message)
            return

    def _schedule_visible_refresh(self) -> None:
        if self._refresh_timer.isActive():
            return
        self._refresh_timer.start()

    def _flush_pending_refresh(self) -> None:
        if not self._pending_index_refresh:
            return
        if not self.isVisible():
            return
        if self._loading:
            self._schedule_visible_refresh()
            return
        self.reload(preserve_content=bool(self._summaries or self._groups))

    def _apply_theme_styles(self) -> None:
        dark_mode = self._uses_dark_theme()
        title_color = "#F5F5F7" if dark_mode else "#111111"
        section_color = "#F5F5F7" if dark_mode else "#111111"
        message_color = "#B7C2DD" if dark_mode else "#63739A"
        refresh_color = "#65A3FF" if dark_mode else "#356CB4"
        empty_text = "#C8D0E4" if dark_mode else "#667085"
        empty_border = "rgba(245, 245, 247, 0.16)" if dark_mode else "rgba(16, 24, 40, 0.14)"
        empty_bg = "rgba(255, 255, 255, 0.06)" if dark_mode else "rgba(255, 255, 255, 0.72)"

        self._title.setStyleSheet(f"color: {title_color}; font-size: 18px; font-weight: 700;")
        for label in (self._groups_title, self._people_title):
            label.setStyleSheet(f"color: {section_color}; font-size: 18px; font-weight: 800;")
        self._message.setStyleSheet(f"color: {message_color}; font-size: 13px;")
        self._refresh_button.setStyleSheet(f"""
            QToolButton {{
                border: none;
                color: {refresh_color};
                font-size: 15px;
                font-weight: 600;
                padding: 6px 10px;
            }}
            """)
        self._empty.setStyleSheet(f"""
            QLabel {{
                padding: 32px;
                border: 1px dashed {empty_border};
                border-radius: 24px;
                color: {empty_text};
                background: {empty_bg};
            }}
            """)

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

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if hasattr(self, "_people_title"):
            self._apply_theme_styles()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._pending_index_refresh or self._loaded_index_version < self._index_version:
            self._schedule_visible_refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._refresh_timer.stop()
