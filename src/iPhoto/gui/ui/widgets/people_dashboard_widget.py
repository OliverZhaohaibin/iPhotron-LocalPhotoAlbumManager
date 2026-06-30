"""Main People dashboard widget."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QGuiApplication
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

from iPhoto.bootstrap.library_people_service import create_people_service
from iPhoto.bootstrap.library_pet_service import create_pet_service
from iPhoto.gui.i18n import tr
from iPhoto.gui.services.pinned_items_service import PinnedItemsService
from iPhoto.people.repository import PeopleGroupSummary, PersonSummary
from iPhoto.people.service import PeopleService
from iPhoto.pets.records import PetSummary
from iPhoto.pets.service import PetService

from ..menus.core import MenuActionSpec, MenuContext, populate_menu
from ..menus.style import apply_menu_style
from . import dialogs
from .people_dashboard_board import GroupBoard, PeopleBoard
from .people_dashboard_cards import GroupCard, PeopleCard, PetCard
from .people_dashboard_dialogs import GroupPeopleDialog, MergeConfirmDialog
from .people_dashboard_shared import (
    _widget_uses_dark_theme,
    configure_people_cover_cache,
)

logger = logging.getLogger(__name__)
_LOCKED_RETRY_INTERVAL_MS = 1500


@dataclass(frozen=True)
class _IdentityChoice:
    person_id: str
    name: str | None
    thumbnail_path: Path | None
    face_count: int


class _PeopleDashboardLoaderSignals(QObject):
    loaded = Signal(int, int, bool, list, list, list, int, int, object, object)
    failed = Signal(int, int, object, bool)


class _PeopleDashboardLoaderWorker(QRunnable):
    def __init__(
        self,
        *,
        generation: int,
        index_version: int,
        people_service: PeopleService,
        pet_service: PetService,
        status_message: str | None,
        pet_status_message: str | None,
        show_hidden_people: bool,
        signals: _PeopleDashboardLoaderSignals,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._index_version = index_version
        self._people_service = people_service
        self._pet_service = pet_service
        self._status_message = status_message
        self._pet_status_message = pet_status_message
        self._show_hidden_people = bool(show_hidden_people)
        self._signals = signals

    def run(self) -> None:
        try:
            self._run()
        except sqlite3.OperationalError as exc:
            locked = "database is locked" in str(exc).lower()
            if locked:
                logger.info("People & Pets dashboard load deferred while SQLite DB is locked")
            else:
                logger.exception("People & Pets dashboard load failed due to SQLite error")
            self._signals.failed.emit(self._generation, self._index_version, exc, locked)
        except Exception as exc:
            logger.exception("People & Pets dashboard load failed")
            self._signals.failed.emit(self._generation, self._index_version, exc, False)

    def _run(self) -> None:
        if self._people_service.library_root() is None:
            self._signals.loaded.emit(
                self._generation,
                self._index_version,
                False,
                [],
                [],
                [],
                0,
                0,
                self._status_message,
                self._pet_status_message,
            )
            return
        summaries, groups, pending = self._people_service.load_dashboard(
            include_hidden=self._show_hidden_people
        )
        pet_summaries, pet_pending = self._pet_service.load_dashboard(
            include_hidden=self._show_hidden_people
        )
        self._signals.loaded.emit(
            self._generation,
            self._index_version,
            True,
            summaries,
            groups,
            pet_summaries,
            pending,
            pet_pending,
            self._status_message,
            self._pet_status_message,
        )


class PeopleDashboardWidget(QWidget):
    clusterActivated = Signal(str)  # noqa: N815
    groupActivated = Signal(str)  # noqa: N815
    petActivated = Signal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PeopleService()
        self._pet_service = PetService()
        self._pinned_service: PinnedItemsService | None = None
        self._status_message: str | None = None
        self._pet_status_message: str | None = None
        self._summaries: list[PersonSummary] = []
        self._groups: list[PeopleGroupSummary] = []
        self._pet_summaries: list[PetSummary] = []
        self._cards: dict[str, PeopleCard] = {}
        self._group_cards: dict[str, GroupCard] = {}
        self._pet_cards: dict[str, PetCard] = {}
        self._load_generation = 0
        self._loading = False
        self._last_pending_faces = 0
        self._last_pending_pets = 0
        self._index_version = 0
        self._loaded_index_version = -1
        self._pending_index_refresh = False
        self._current_library_root: Path | None = None
        self._show_hidden_people = False
        self._load_signals = _PeopleDashboardLoaderSignals()
        self._load_signals.loaded.connect(self._on_load_completed)
        self._load_signals.failed.connect(self._on_load_failed)
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

        self._title = QLabel()
        self._title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 700;")

        self._refresh_button = QToolButton()
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

        self._groups_title = QLabel()
        self._groups_title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 800;")
        groups_layout.addWidget(self._groups_title)

        self._groups_host = QWidget()
        self._groups_host.setStyleSheet("background: transparent;")
        self._groups_layout = QVBoxLayout(self._groups_host)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(0)
        self._groups_board = GroupBoard()
        self._groups_board.orderChanged.connect(self._persist_group_order)
        self._groups_layout.addWidget(self._groups_board)
        groups_layout.addWidget(self._groups_host)
        self._content_layout.addWidget(self._groups_section)

        self._people_title = QLabel()
        self._people_title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 800;")
        self._content_layout.addWidget(self._people_title)

        self._board = PeopleBoard()
        self._board.mergeRequested.connect(self._merge_cluster_pair)
        self._board.orderChanged.connect(self._persist_cluster_order)
        self._content_layout.addWidget(self._board)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        self._show_hidden_people = self._load_show_hidden_people_setting()
        self.retranslate_ui()
        self._apply_theme_styles()

    def set_people_service(self, service: PeopleService | None) -> None:
        self._service = service or PeopleService()
        self._current_library_root = self._service.library_root()
        configure_people_cover_cache(self._current_library_root)
        self.reload()

    def set_pet_service(self, service: PetService | None) -> None:
        self._pet_service = service or PetService()
        self.reload(preserve_content=bool(self._summaries or self._pet_summaries or self._groups))

    def set_library_root(self, library_root: Path | None) -> None:
        service_matches_root = self._service.library_root() == library_root
        service_has_asset_boundary = (
            library_root is None or self._service.asset_repository is not None
        )
        if (
            self._current_library_root == library_root
            and service_matches_root
            and service_has_asset_boundary
        ):
            return
        self._current_library_root = library_root
        self._service = (
            create_people_service(library_root) if library_root is not None else PeopleService()
        )
        self._pet_service = (
            create_pet_service(library_root) if library_root is not None else PetService()
        )
        configure_people_cover_cache(library_root)
        self.reload()

    def set_pinned_service(self, service: PinnedItemsService | None) -> None:
        self._pinned_service = service

    def set_show_hidden_people(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._show_hidden_people == enabled:
            return
        self._show_hidden_people = enabled
        self.reload(preserve_content=bool(self._summaries or self._groups))

    def build_cluster_query(self, person_id: str):
        return self._service.build_cluster_query(person_id)

    def build_group_query(self, group_id: str):
        return self._service.build_group_query(group_id)

    def build_pet_query(self, pet_id: str):
        return self._pet_service.build_pet_query(pet_id)

    def set_status_message(self, message: str | None) -> None:
        self._status_message = message or None
        self._update_status_labels()

    def set_pet_status_message(self, message: str | None) -> None:
        self._pet_status_message = message or None
        self._update_status_labels()

    def retranslate_ui(self) -> None:
        self._title.setText(tr("PeopleDashboard", "People"))
        self._refresh_button.setText(tr("PeopleDashboard", "Refresh"))
        self._groups_title.setText(tr("PeopleDashboard", "Groups"))
        self._people_title.setText(tr("PeopleDashboard", "People & Pets"))
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
            self._last_pending_faces = 0
            self._last_pending_pets = 0
            self._loaded_index_version = index_version
            self._set_unbound_message()
            self._empty.show()
            self._scroll.hide()
            return

        if not preserve_content or (not self._summaries and not self._groups):
            self._set_loading_message()
            self._empty.show()
            self._scroll.hide()

        worker = _PeopleDashboardLoaderWorker(
            generation=generation,
            index_version=index_version,
            people_service=self._service,
            pet_service=self._pet_service,
            status_message=self._status_message,
            pet_status_message=self._pet_status_message,
            show_hidden_people=self._show_hidden_people,
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
        pet_summaries: list[PetSummary],
        pending: int,
        pet_pending: int,
        status_message: str | None,
        pet_status_message: str | None,
    ) -> None:
        if generation != self._load_generation:
            return
        self._loading = False
        if not is_bound:
            self._loaded_index_version = index_version
            self._last_pending_faces = 0
            self._last_pending_pets = 0
            self._set_unbound_message()
            self._empty.show()
            self._scroll.hide()
            return

        next_summaries = list(summaries)
        next_groups = list(groups)
        next_pet_summaries = list(pet_summaries)
        cards_changed = (
            next_summaries != self._summaries
            or next_groups != self._groups
            or next_pet_summaries != self._pet_summaries
        )
        self._summaries = next_summaries
        self._groups = next_groups
        self._pet_summaries = next_pet_summaries
        self._last_pending_faces = int(pending)
        self._last_pending_pets = int(pet_pending)
        self._loaded_index_version = index_version
        self._pending_index_refresh = False
        status_text = status_message if status_message else self._status_message
        pet_status_text = pet_status_message if pet_status_message else self._pet_status_message

        if self._summaries or self._pet_summaries:
            self._set_populated_message()
            self._empty.hide()
            self._scroll.show()
            if cards_changed:
                self._populate_groups()
                self._populate_cards()
            return

        if status_text or pet_status_text:
            body = "\n".join(text for text in (status_text, pet_status_text) if text)
        elif pending > 0 or pet_pending > 0:
            body = self._scanning_message()
        else:
            body = self._empty_clusters_message()
        self._message.setText(body)
        self._empty.setText(body)
        self._empty.show()
        self._scroll.hide()
        self._clear_group_cards()
        self._clear_cards()

        if self._loaded_index_version < self._index_version:
            self._schedule_visible_refresh()

    def _on_load_failed(
        self,
        generation: int,
        index_version: int,
        error: object,
        retryable: bool,
    ) -> None:
        if generation != self._load_generation:
            return
        self._loading = False
        if retryable:
            self._pending_index_refresh = True
            self._set_database_busy_message()
            if not self._summaries and not self._pet_summaries and not self._groups:
                self._empty.show()
                self._scroll.hide()
            if self.isVisible():
                self._refresh_timer.start(_LOCKED_RETRY_INTERVAL_MS)
            return

        self._loaded_index_version = index_version
        self._set_load_failed_message(error)
        if not self._summaries and not self._pet_summaries and not self._groups:
            self._empty.show()
            self._scroll.hide()

    def _populate_groups(self) -> None:
        self._clear_group_cards()
        if not self._groups:
            self._groups_section.hide()
            return

        self._groups_section.show()
        cards: list[GroupCard] = []
        for index, summary in enumerate(self._groups):
            card = GroupCard(
                board=self._groups_board,
                summary=summary,
                seed_index=index,
            )
            card.activated.connect(self.groupActivated.emit)
            card.menuRequested.connect(self._show_group_menu)
            self._group_cards[summary.group_id] = card
            cards.append(card)
        self._groups_board.set_cards(cards)
        for card in cards:
            card.load_cover_artwork()
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
        offset = len(cards)
        for index, summary in enumerate(self._pet_summaries):
            card = PetCard(
                board=self._board,
                summary=summary,
                seed_index=offset + index,
            )
            card.activated.connect(self.petActivated.emit)
            card.menuRequested.connect(self._show_card_menu)
            self._pet_cards[summary.pet_id] = card
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
        self._pet_cards.clear()

    def _clear_group_cards(self) -> None:
        self._groups_board.clear_cards()
        self._group_cards.clear()

    def _summary_for_person(self, person_id: str) -> PersonSummary | None:
        return next((item for item in self._summaries if item.person_id == person_id), None)

    def _summary_for_pet(self, pet_id: str) -> PetSummary | None:
        return next((item for item in self._pet_summaries if item.pet_id == pet_id), None)

    def _show_card_menu(self, person_id: str, global_pos) -> None:
        summary = self._summary_for_person(person_id)
        if summary is None:
            pet_summary = self._summary_for_pet(person_id)
            if pet_summary is None:
                return
            menu = self._build_pet_menu(pet_summary)
            menu.exec(global_pos)
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
        apply_menu_style(menu, self)
        merge_enabled = any(
            target.person_id != summary.person_id and target.is_hidden == summary.is_hidden
            for target in self._summaries
        )
        context = MenuContext(
            surface="people_dashboard",
            selection_kind="empty",
            entity_kind="person",
            entity_id=summary.person_id,
        )
        populate_menu(
            menu,
            context=context,
            action_specs=[
                MenuActionSpec(
                    action_id="rename_person",
                    label=(
                        tr("PeopleDashboard", "Rename")
                        if summary.name
                        else tr("PeopleDashboard", "Name This Person")
                    ),
                    on_trigger=lambda _ctx: self._rename_person(summary),
                ),
                MenuActionSpec(
                    action_id="new_group",
                    label=tr("PeopleDashboard", "New Group"),
                    on_trigger=lambda _ctx: self._open_group_dialog(f"person:{summary.person_id}"),
                ),
                MenuActionSpec(
                    action_id="toggle_hidden",
                    label=(
                        tr("PeopleDashboard", "Unhide")
                        if summary.is_hidden
                        else tr("PeopleDashboard", "Hide")
                    ),
                    on_trigger=lambda _ctx: self._toggle_person_hidden(summary),
                ),
                MenuActionSpec(
                    action_id="toggle_pin",
                    label=(
                        tr("PeopleDashboard", "Unpin")
                        if self._is_person_pinned(summary.person_id)
                        else tr("PeopleDashboard", "Pin")
                    ),
                    on_trigger=lambda _ctx: self._toggle_person_pin(summary),
                    is_enabled=lambda _ctx: self._pin_actions_available(),
                ),
                MenuActionSpec(
                    action_id="merge",
                    label=tr("PeopleDashboard", "Merge Into..."),
                    on_trigger=lambda _ctx: self._merge_person(summary),
                    is_enabled=lambda _ctx: merge_enabled,
                    separator_before=True,
                ),
            ],
            anchor=self,
        )
        return menu

    def _build_pet_menu(self, summary: PetSummary) -> QMenu:
        menu = QMenu(self)
        apply_menu_style(menu, self)
        merge_enabled = any(
            target.pet_id != summary.pet_id
            and target.species_label == summary.species_label
            and target.is_hidden == summary.is_hidden
            for target in self._pet_summaries
        )
        context = MenuContext(
            surface="people_dashboard",
            selection_kind="empty",
            entity_kind="pet",
            entity_id=summary.pet_id,
        )
        populate_menu(
            menu,
            context=context,
            action_specs=[
                MenuActionSpec(
                    action_id="rename_pet",
                    label=(
                        tr("PeopleDashboard", "Rename")
                        if summary.name
                        else tr("PeopleDashboard", "Name This Pet")
                    ),
                    on_trigger=lambda _ctx: self._rename_pet(summary),
                ),
                MenuActionSpec(
                    action_id="new_group",
                    label=tr("PeopleDashboard", "New Group"),
                    on_trigger=lambda _ctx: self._open_group_dialog(f"pet:{summary.pet_id}"),
                ),
                MenuActionSpec(
                    action_id="toggle_pet_hidden",
                    label=(
                        tr("PeopleDashboard", "Unhide")
                        if summary.is_hidden
                        else tr("PeopleDashboard", "Hide")
                    ),
                    on_trigger=lambda _ctx: self._toggle_pet_hidden(summary),
                ),
                MenuActionSpec(
                    action_id="toggle_pet_pin",
                    label=(
                        tr("PeopleDashboard", "Unpin")
                        if self._is_pet_pinned(summary.pet_id)
                        else tr("PeopleDashboard", "Pin")
                    ),
                    on_trigger=lambda _ctx: self._toggle_pet_pin(summary),
                    is_enabled=lambda _ctx: self._pin_actions_available(),
                ),
                MenuActionSpec(
                    action_id="merge_pet",
                    label=tr("PeopleDashboard", "Merge Into..."),
                    on_trigger=lambda _ctx: self._merge_pet(summary),
                    is_enabled=lambda _ctx: merge_enabled,
                    separator_before=True,
                ),
                MenuActionSpec(
                    action_id="set_pet_cover",
                    label=tr("PeopleDashboard", "Set as Cover"),
                    on_trigger=lambda _ctx: self._set_pet_cover(summary),
                    is_enabled=lambda _ctx: bool(summary.key_detection_id),
                ),
                MenuActionSpec(
                    action_id="delete_pet_detection",
                    label=tr("PeopleDashboard", "Delete Detection"),
                    on_trigger=lambda _ctx: self._delete_pet_detection(summary),
                    is_enabled=lambda _ctx: bool(summary.key_detection_id),
                    separator_before=True,
                ),
            ],
            anchor=self,
        )
        return menu

    def _build_group_menu(self, summary: PeopleGroupSummary) -> QMenu:
        menu = QMenu(self)
        apply_menu_style(menu, self)
        context = MenuContext(
            surface="people_dashboard",
            selection_kind="empty",
            entity_kind="group",
            entity_id=summary.group_id,
        )
        populate_menu(
            menu,
            context=context,
            action_specs=[
                MenuActionSpec(
                    action_id="toggle_group_pin",
                    label=(
                        tr("PeopleDashboard", "Unpin")
                        if self._is_group_pinned(summary.group_id)
                        else tr("PeopleDashboard", "Pin")
                    ),
                    on_trigger=lambda _ctx: self._toggle_group_pin(summary),
                    is_enabled=lambda _ctx: self._pin_actions_available(),
                ),
                MenuActionSpec(
                    action_id="disband_group",
                    label=tr("PeopleDashboard", "Disband Group"),
                    on_trigger=lambda _ctx: self._disband_group(summary),
                    separator_before=True,
                ),
            ],
            anchor=self,
        )
        return menu

    def _rename_person(self, summary: PersonSummary) -> None:
        title = (
            tr("PeopleDashboard", "Rename Person")
            if summary.name
            else tr("PeopleDashboard", "Name This Person")
        )
        text, accepted = QInputDialog.getText(
            self,
            title,
            tr("PeopleDashboard", "Name:"),
            text=summary.name or "",
        )
        if not accepted:
            return
        self._service.rename_cluster(summary.person_id, text.strip() or None)
        self.reload(preserve_content=bool(self._summaries))

    def _rename_pet(self, summary: PetSummary) -> None:
        title = (
            tr("PeopleDashboard", "Rename Pet")
            if summary.name
            else tr("PeopleDashboard", "Name This Pet")
        )
        text, accepted = QInputDialog.getText(
            self,
            title,
            tr("PeopleDashboard", "Name:"),
            text=summary.name or "",
        )
        if not accepted:
            return
        self._pet_service.rename_pet(summary.pet_id, text.strip() or None)
        self.reload(preserve_content=bool(self._summaries or self._pet_summaries))

    def _toggle_pet_hidden(self, summary: PetSummary) -> None:
        next_hidden = not summary.is_hidden
        if next_hidden and not self._confirm_hide_pet(summary):
            return
        changed = self._pet_service.set_pet_hidden(summary.pet_id, next_hidden)
        if changed:
            self.reload(
                preserve_content=bool(self._summaries or self._pet_summaries or self._groups)
            )

    def _merge_pet(self, summary: PetSummary) -> None:
        choices = [
            target
            for target in self._pet_summaries
            if target.pet_id != summary.pet_id
            and target.species_label == summary.species_label
            and target.is_hidden == summary.is_hidden
        ]
        if not choices:
            return
        labels = [self._pet_label(choice) for choice in choices]
        selected, accepted = QInputDialog.getItem(
            self,
            tr("PeopleDashboard", "Merge Pet"),
            tr("PeopleDashboard", "Merge into"),
            labels,
            0,
            False,
        )
        if not accepted or not selected:
            return
        target = choices[labels.index(selected)]
        if not MergeConfirmDialog.confirm(2, self):
            return
        if self._pet_service.merge_pets(summary.pet_id, target.pet_id):
            self.reload(preserve_content=bool(self._summaries or self._pet_summaries))

    def _set_pet_cover(self, summary: PetSummary) -> None:
        if summary.key_detection_id and self._pet_service.set_pet_cover(
            summary.pet_id,
            summary.key_detection_id,
        ):
            self.reload(preserve_content=bool(self._summaries or self._pet_summaries))

    def _delete_pet_detection(self, summary: PetSummary) -> None:
        if not summary.key_detection_id:
            return
        if not MergeConfirmDialog.confirm_action(
            item_count=1,
            parent=self,
            title_text=tr("PeopleDashboard", "Delete This Detection?"),
            body_text=tr(
                "PeopleDashboard",
                "Deleting this pet detection will hide this matched crop after future rescans.",
            ),
            confirm_text=tr("PeopleDashboard", "Delete Detection"),
        ):
            return
        if self._pet_service.delete_detection(summary.key_detection_id):
            self.reload(preserve_content=bool(self._summaries or self._pet_summaries))

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

    def _toggle_person_hidden(self, summary: PersonSummary) -> None:
        next_hidden = not summary.is_hidden
        if next_hidden and not self._confirm_hide_person(summary):
            return
        changed = self._service.set_cluster_hidden(summary.person_id, next_hidden)
        if changed:
            self.reload(preserve_content=bool(self._summaries or self._groups))

    def _toggle_pet_pin(self, summary: PetSummary) -> None:
        if self._pinned_service is None:
            return
        library_root = self._service.library_root()
        if library_root is None:
            return
        if self._is_pet_pinned(summary.pet_id):
            self._pinned_service.unpin(
                kind="pet",
                item_id=summary.pet_id,
                library_root=library_root,
            )
            return
        self._pinned_service.pin_pet(
            summary.pet_id,
            self._pet_label(summary),
            library_root=library_root,
        )

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

    def _disband_group(self, summary: PeopleGroupSummary) -> None:
        if self._is_group_pinned(summary.group_id):
            dialogs.show_warning(
                self,
                tr("PeopleDashboard", "Pinned groups can't be disbanded until they are unpinned."),
            )
            return
        if not self._confirm_disband_group(summary):
            return
        if self._service.delete_group(summary.group_id):
            self.reload(preserve_content=bool(self._summaries or self._groups))

    def _merge_person(self, summary: PersonSummary) -> None:
        has_other_people = any(target.person_id != summary.person_id for target in self._summaries)
        choices = [
            target
            for target in self._summaries
            if target.person_id != summary.person_id and target.is_hidden == summary.is_hidden
        ]
        if not choices:
            if has_other_people:
                dialogs.show_information(
                    self,
                    self._hidden_state_merge_message(),
                    title=tr("PeopleDashboard", "Cannot Merge People"),
                )
            return

        dialog = GroupPeopleDialog(
            choices,
            title_text=tr("PeopleDashboard", "Merge Person"),
            prompt_text=tr("PeopleDashboard", "Merge into"),
            confirm_text=tr("PeopleDashboard", "Choose"),
            min_selection=1,
            max_selection=1,
            dark_mode=self._uses_dark_theme(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_ids = dialog.selected_person_ids()
        if not selected_ids:
            return
        self._confirm_merge(summary.person_id, selected_ids[0])

    def _open_group_dialog(self, initial_person_id: str) -> None:
        choices = self._group_dialog_choices()
        if len(choices) < 2:
            return
        dialog = GroupPeopleDialog(
            choices,  # type: ignore[arg-type]
            initial_selected_ids=[initial_person_id],
            dark_mode=self._uses_dark_theme(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        group = self._service.create_group(dialog.selected_person_ids())
        if group is not None:
            self.reload(preserve_content=bool(self._summaries))

    def _group_dialog_choices(self) -> list[_IdentityChoice]:
        choices: list[_IdentityChoice] = []
        for summary in self._summaries:
            choices.append(
                _IdentityChoice(
                    person_id=f"person:{summary.person_id}",
                    name=summary.name or tr("PeopleDashboard", "Unnamed"),
                    thumbnail_path=summary.thumbnail_path,
                    face_count=summary.face_count,
                )
            )
        for summary in self._pet_summaries:
            choices.append(
                _IdentityChoice(
                    person_id=f"pet:{summary.pet_id}",
                    name=self._pet_label(summary),
                    thumbnail_path=summary.thumbnail_path,
                    face_count=summary.detection_count,
                )
            )
        return choices

    def _merge_cluster_pair(self, source_person_id: str, target_person_id: str) -> None:
        self._confirm_merge(source_person_id, target_person_id)

    def _confirm_merge(self, source_person_id: str, target_person_id: str) -> bool:
        if source_person_id == target_person_id:
            return False

        source = self._summary_for_person(source_person_id)
        target = self._summary_for_person(target_person_id)
        if source is None or target is None:
            return False
        if source.is_hidden != target.is_hidden:
            dialogs.show_information(
                self,
                self._hidden_state_merge_message(),
                title=tr("PeopleDashboard", "Cannot Merge People"),
            )
            return False

        if not MergeConfirmDialog.confirm(2, self):
            return False

        merged = self._service.merge_clusters(source_person_id, target_person_id)
        if merged:
            self.reload(preserve_content=bool(self._summaries))
        return merged

    def _confirm_hide_person(self, summary: PersonSummary) -> bool:
        name = (summary.name or "").strip() or tr("PeopleDashboard", "this person")
        return MergeConfirmDialog.confirm_action(
            item_count=1,
            parent=self,
            title_text=tr("PeopleDashboard", "Hide This Person?"),
            body_text=tr(
                "PeopleDashboard",
                "Hiding {name} will remove them from the People view until you choose "
                "Show Hidden People or unhide them.",
            ).format(name=name),
            confirm_text=tr("PeopleDashboard", "Hide Person"),
        )

    def _confirm_hide_pet(self, summary: PetSummary) -> bool:
        name = self._pet_label(summary)
        return MergeConfirmDialog.confirm_action(
            item_count=1,
            parent=self,
            title_text=tr("PeopleDashboard", "Hide This Pet?"),
            body_text=tr(
                "PeopleDashboard",
                "Hiding {name} will remove this pet from the People & Pets view until "
                "you choose Show Hidden People or unhide it.",
            ).format(name=name),
            confirm_text=tr("PeopleDashboard", "Hide Pet"),
        )

    def _confirm_disband_group(self, summary: PeopleGroupSummary) -> bool:
        label = summary.name.strip() or tr("PeopleDashboard", "this group")
        return MergeConfirmDialog.confirm_action(
            item_count=max(2, len(summary.member_entities) or len(summary.member_person_ids)),
            parent=self,
            title_text=tr("PeopleDashboard", "Disband This Group?"),
            body_text=tr(
                "PeopleDashboard",
                "Disbanding {label} will remove the group but keep all of its people and photos.",
            ).format(label=label),
            confirm_text=tr("PeopleDashboard", "Disband Group"),
        )

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

    def _persist_group_order(self, ordered_group_ids: list[str]) -> None:
        current_ids = {summary.group_id for summary in self._groups}
        filtered = [group_id for group_id in ordered_group_ids if group_id in current_ids]
        if len(filtered) != len(self._groups):
            filtered.extend(
                summary.group_id
                for summary in self._groups
                if summary.group_id not in set(filtered)
            )
        if filtered:
            self._service.set_group_order(filtered)

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

    def _is_pet_pinned(self, pet_id: str) -> bool:
        if self._pinned_service is None:
            return False
        return self._pinned_service.is_pinned(
            kind="pet",
            item_id=pet_id,
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
        title = tr("PeopleDashboard", "Name This Person")
        text, accepted = QInputDialog.getText(
            self,
            title,
            tr("PeopleDashboard", "Name:"),
            text=summary.name or "",
        )
        if not accepted:
            return None
        normalized = text.strip()
        if normalized:
            return normalized
        dialogs.show_warning(
            self,
            tr("PeopleDashboard", "A name is required before pinning this person."),
        )
        return None

    def _update_status_labels(self) -> None:
        if self._loading:
            if not self._summaries and not self._pet_summaries and not self._groups:
                self._set_loading_message()
            return
        if self._service.library_root() is None:
            self._set_unbound_message()
            return
        if self._summaries or self._pet_summaries:
            self._set_populated_message()
            return
        if self._status_message or self._pet_status_message:
            text = "\n".join(
                item for item in (self._status_message, self._pet_status_message) if item
            )
            self._message.setText(text)
            self._empty.setText(text)
            return
        body = (
            self._scanning_message()
            if self._last_pending_faces > 0 or self._last_pending_pets > 0
            else self._empty_clusters_message()
        )
        self._message.setText(body)
        self._empty.setText(body)

    def _set_unbound_message(self) -> None:
        self._message.setText(
            tr("PeopleDashboard", "Bind a Basic Library to see People & Pets clusters.")
        )
        self._empty.setText(
            tr("PeopleDashboard", "People & Pets appear here after a library is bound and scanned.")
        )

    def _set_loading_message(self) -> None:
        text = tr("PeopleDashboard", "Loading People & Pets dashboard…")
        self._message.setText(text)
        self._empty.setText(text)

    def _set_database_busy_message(self) -> None:
        text = tr(
            "PeopleDashboard",
            "People & Pets is updating in the background. This page will retry shortly.",
        )
        self._message.setText(text)
        self._empty.setText(text)

    def _set_load_failed_message(self, error: object) -> None:
        del error
        text = tr(
            "PeopleDashboard",
            "People & Pets could not be loaded. Please try refreshing the page.",
        )
        self._message.setText(text)
        self._empty.setText(text)

    def _set_populated_message(self) -> None:
        self._message.setText(
            tr(
                "PeopleDashboard",
                "Click a person, pet, or group card to open matching assets.",
            )
        )

    def _scanning_message(self) -> str:
        return tr(
            "PeopleDashboard",
            "Scanning faces and pets in the background. This page will fill in as "
            "clusters are ready.",
        )

    def _empty_clusters_message(self) -> str:
        return tr("PeopleDashboard", "No People or Pets clusters yet. Run a scan to build groups.")

    def _hidden_state_merge_message(self) -> str:
        return tr(
            "PeopleDashboard",
            "People in hidden and visible states cannot be merged. Please make both People "
            "cards hidden or visible first.",
        )

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
        self.reload(preserve_content=bool(self._summaries or self._pet_summaries or self._groups))

    def _pet_label(self, summary: PetSummary) -> str:
        name = (summary.name or "").strip()
        if name:
            return name
        species = str(summary.species_label or "").strip().title()
        return tr("PeopleDashboard", "Unnamed {species}").format(
            species=species or tr("PeopleDashboard", "Pet")
        )

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

    def _load_show_hidden_people_setting(self) -> bool:
        window = self.window()
        coordinator = getattr(window, "coordinator", None)
        context = getattr(coordinator, "_context", None)
        settings = getattr(context, "settings", None)
        if settings is None or not hasattr(settings, "get"):
            return False
        stored = settings.get("ui.show_hidden_people", False)
        if isinstance(stored, str):
            return stored.strip().lower() in {"1", "true", "yes", "on"}
        return bool(stored)

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
