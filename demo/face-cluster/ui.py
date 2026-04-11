from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_imports import import_sibling


_db = import_sibling("db")
_image_utils = import_sibling("image_utils")
_worker = import_sibling("worker")

FaceClusterRepository = _db.FaceClusterRepository
PersonSummary = _db.PersonSummary
render_round_pixmap = _image_utils.render_round_pixmap
FaceClusterWorker = _worker.FaceClusterWorker
WorkerResult = _worker.WorkerResult


TABLE_COLUMNS = [
    "face_id",
    "asset_rel",
    "box_x",
    "box_y",
    "box_w",
    "box_h",
    "confidence",
    "embedding_dim",
    "embedding_bytes",
    "thumbnail_path",
    "person_id",
    "detected_at",
    "image_width",
    "image_height",
]


class ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.IBeamCursor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class EditableNameLabel(QWidget):
    rename_submitted = Signal(str)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._editing = False

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

        self._display_label = ClickableLabel(text)
        self._display_label.setAlignment(Qt.AlignCenter)
        self._display_label.setStyleSheet("font-weight: 600;")
        self._display_label.clicked.connect(self._begin_editing)

        self._editor = QLineEdit(text)
        self._editor.setAlignment(Qt.AlignCenter)
        self._editor.setFrame(False)
        self._editor.setStyleSheet("font-weight: 600; background: transparent;")
        self._editor.returnPressed.connect(self._finish_editing)
        self._editor.editingFinished.connect(self._finish_editing)

        self._stack.addWidget(self._display_label)
        self._stack.addWidget(self._editor)
        self._stack.setCurrentWidget(self._display_label)

    def text(self) -> str:
        return self._display_label.text()

    def set_text(self, text: str) -> None:
        self._display_label.setText(text)
        self._editor.setText(text)

    def _begin_editing(self) -> None:
        self._editing = True
        self._editor.setText(self._display_label.text())
        self._stack.setCurrentWidget(self._editor)
        self._editor.selectAll()
        self._editor.setFocus()

    def _finish_editing(self) -> None:
        if not self._editing:
            return
        self._editing = False
        new_text = self._editor.text().strip()
        self._stack.setCurrentWidget(self._display_label)
        self.rename_submitted.emit(new_text)


class ClusterCard(QFrame):
    clicked = Signal(str)
    rename_requested = Signal(str, str)
    context_menu_requested = Signal(str, object)

    def __init__(self, summary: PersonSummary, display_name: str, parent=None) -> None:
        super().__init__(parent)
        self._summary = summary
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("ClusterCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self._avatar_label = QLabel()
        self._avatar_label.setAlignment(Qt.AlignCenter)
        self._title_label = EditableNameLabel(display_name)
        self._title_label.rename_submitted.connect(self._emit_rename)

        self._subtitle_label = QLabel()
        self._subtitle_label.setAlignment(Qt.AlignCenter)
        self._subtitle_label.setStyleSheet("color: #6b7280;")

        layout.addWidget(self._avatar_label, alignment=Qt.AlignCenter)
        layout.addWidget(self._title_label)
        layout.addWidget(self._subtitle_label)
        self.set_summary(summary, display_name)
        self._refresh_style()

    @property
    def person_id(self) -> str:
        return self._summary.person_id

    def set_summary(self, summary: PersonSummary, display_name: str) -> None:
        self._summary = summary
        self._title_label.set_text(display_name)
        self._subtitle_label.setText(f"{summary.face_count} 张人脸")
        if summary.thumbnail_path and summary.thumbnail_path.exists():
            self._avatar_label.setPixmap(render_round_pixmap(summary.thumbnail_path, size=112))
            self._avatar_label.setText("")
        else:
            self._avatar_label.clear()
            self._avatar_label.setText("无头像")

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_style()

    def build_context_menu(self, *, has_merge_targets: bool, parent=None) -> QMenu:
        menu = QMenu(parent)
        merge_action = QAction("合并到...", menu)
        merge_action.setEnabled(has_merge_targets)
        menu.addAction(merge_action)
        return menu

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._summary.person_id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self.context_menu_requested.emit(self._summary.person_id, event.globalPos())
        event.accept()

    def _emit_rename(self, new_name: str) -> None:
        self.rename_requested.emit(self._summary.person_id, new_name)

    def _refresh_style(self) -> None:
        if self._selected:
            border = "#2563eb"
            background = "#eff6ff"
        else:
            border = "#d1d5db"
            background = "#ffffff"
        self.setStyleSheet(
            f"""
            QFrame#ClusterCard {{
                border: 2px solid {border};
                border-radius: 18px;
                background: {background};
            }}
            """
        )


class FaceClusterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Face Cluster MVP")
        self.resize(1280, 860)

        self._worker: FaceClusterWorker | None = None
        self._repository: FaceClusterRepository | None = None
        self._cards: dict[str, ClusterCard] = {}
        self._current_summaries: list[PersonSummary] = []
        self._display_names: dict[str, str] = {}
        self._selected_person_id: str | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)
        self._bind_button = QPushButton("绑定文件夹")
        self._bind_button.clicked.connect(self._choose_folder)

        self._folder_label = QLabel("尚未绑定文件夹")
        self._folder_label.setStyleSheet("color: #4b5563;")
        self._folder_label.setWordWrap(True)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(1)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedWidth(220)

        top_bar.addWidget(self._bind_button)
        top_bar.addWidget(self._folder_label, stretch=1)
        top_bar.addWidget(self._progress_bar)
        root_layout.addLayout(top_bar)

        self._status_label = QLabel("点击“绑定文件夹”开始扫描。")
        self._status_label.setStyleSheet("color: #6b7280;")
        root_layout.addWidget(self._status_label)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter, stretch=1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        title = QLabel("聚类结果")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        left_layout.addWidget(title)

        self._empty_label = QLabel("尚未扫描任何目录。")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(
            "padding: 24px; border: 1px dashed #cbd5e1; border-radius: 16px; color: #64748b;"
        )
        left_layout.addWidget(self._empty_label)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._grid_host = QWidget()
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(12)
        self._scroll_area.setWidget(self._grid_host)
        left_layout.addWidget(self._scroll_area, stretch=1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        detail_title = QLabel("faces 表记录")
        detail_title.setFont(title_font)
        right_layout.addWidget(detail_title)

        self._detail_hint = QLabel("点击左侧圆形头像后，这里会展示对应聚类的人脸记录。")
        self._detail_hint.setStyleSheet("color: #6b7280;")
        self._detail_hint.setWordWrap(True)
        right_layout.addWidget(self._detail_hint)

        self._table = QTableWidget(0, len(TABLE_COLUMNS))
        self._table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self._table, stretch=1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([560, 720])

    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择待聚类的文件夹", "")
        if not selected:
            return
        self._start_scan(Path(selected))

    def _start_scan(self, folder: Path) -> None:
        self._bind_button.setEnabled(False)
        self._folder_label.setText(str(folder))
        self._status_label.setText("准备扫描目录…")
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(1)
        self._clear_cards()
        self._clear_table()
        self._current_summaries = []
        self._selected_person_id = None
        self._empty_label.setText("正在扫描，请稍候…")
        self._empty_label.show()

        worker = FaceClusterWorker(folder, self)
        worker.progress_changed.connect(self._on_progress_changed)
        worker.status_changed.connect(self._status_label.setText)
        worker.failed.connect(self._on_scan_failed)
        worker.finished_with_result.connect(self._on_scan_finished)
        self._worker = worker
        worker.start()

    def _on_progress_changed(self, current: int, total: int) -> None:
        self._progress_bar.setMaximum(max(1, total))
        self._progress_bar.setValue(min(current, max(1, total)))

    def _on_scan_failed(self, message: str) -> None:
        self._bind_button.setEnabled(True)
        self._worker = None
        self._status_label.setText("扫描失败。")
        self._empty_label.setText("扫描失败，请重新绑定文件夹。")
        self._empty_label.show()
        QMessageBox.critical(self, "Face Cluster MVP", message)

    def _on_scan_finished(self, result: WorkerResult) -> None:
        self._bind_button.setEnabled(True)
        self._worker = None
        self._repository = FaceClusterRepository(result.workspace.db_path, result.workspace.state_db_path)

        if result.image_count == 0:
            self._status_label.setText("目录内没有可扫描的图片。")
            self._empty_label.setText("目录内没有可扫描的图片。")
            self._empty_label.show()
            return

        if result.face_count == 0:
            warning_suffix = f" 跳过 {len(result.warnings)} 张图片。" if result.warnings else ""
            self._status_label.setText(f"扫描完成，但没有检测到任何人脸。{warning_suffix}")
            self._empty_label.setText("图片已扫描完成，但没有检测到任何人脸。")
            self._empty_label.show()
            return

        warning_suffix = f"，有 {len(result.warnings)} 条警告" if result.warnings else ""
        self._status_label.setText(
            f"扫描完成：{result.image_count} 张图片，{result.face_count} 张人脸，"
            f"{result.cluster_count} 个聚类{warning_suffix}。"
        )
        self._refresh_from_repository(result.person_summaries[0].person_id if result.person_summaries else None)

    def _refresh_from_repository(self, selected_person_id: str | None = None) -> None:
        if self._repository is None:
            return

        summaries = self._repository.get_person_summaries()
        self._current_summaries = summaries
        self._populate_cards(summaries)
        if not summaries:
            self._clear_table()
            self._empty_label.setText("没有可展示的聚类结果。")
            self._empty_label.show()
            self._selected_person_id = None
            return

        valid_ids = {summary.person_id for summary in summaries}
        target_id = selected_person_id if selected_person_id in valid_ids else None
        if target_id is None and self._selected_person_id in valid_ids:
            target_id = self._selected_person_id
        if target_id is None:
            target_id = summaries[0].person_id
        self._select_person(target_id)

    def _populate_cards(self, summaries: list[PersonSummary]) -> None:
        self._clear_cards()
        self._display_names = {
            summary.person_id: summary.name or f"人物{index + 1}"
            for index, summary in enumerate(summaries)
        }
        if not summaries:
            self._empty_label.setText("没有可展示的聚类结果。")
            self._empty_label.show()
            return

        self._empty_label.hide()
        columns = 3
        for index, summary in enumerate(summaries):
            row = index // columns
            column = index % columns
            card = ClusterCard(summary, self._display_names[summary.person_id])
            card.clicked.connect(self._select_person)
            card.rename_requested.connect(self._rename_person)
            card.context_menu_requested.connect(self._show_context_menu)
            self._grid_layout.addWidget(card, row, column)
            self._cards[summary.person_id] = card

        for column in range(columns):
            self._grid_layout.setColumnStretch(column, 1)

    def _select_person(self, person_id: str) -> None:
        if self._repository is None:
            return

        self._selected_person_id = person_id
        for current_id, card in self._cards.items():
            card.set_selected(current_id == person_id)

        rows = self._repository.get_faces_by_person(person_id)
        display_name = self._display_names.get(person_id, person_id[:8])
        self._detail_hint.setText(f"当前人物 {display_name}，共 {len(rows)} 条 faces 表记录。")
        self._table.setRowCount(len(rows))
        for row_index, row_data in enumerate(rows):
            for column_index, column_name in enumerate(TABLE_COLUMNS):
                value = row_data.get(column_name, "")
                if isinstance(value, float):
                    text = f"{value:.4f}"
                else:
                    text = str(value)
                self._table.setItem(row_index, column_index, QTableWidgetItem(text))
        self._table.resizeColumnsToContents()

    def _rename_person(self, person_id: str, new_name: str) -> None:
        if self._repository is None:
            return

        try:
            self._repository.rename_person(person_id, new_name or None)
        except Exception as exc:
            QMessageBox.critical(self, "Face Cluster MVP", str(exc))
            return

        self._refresh_from_repository(person_id)

    def _show_context_menu(self, person_id: str, global_pos: QPoint) -> None:
        card = self._cards.get(person_id)
        if card is None:
            return

        menu = card.build_context_menu(has_merge_targets=len(self._current_summaries) > 1, parent=self)
        merge_action = menu.actions()[0]
        merge_action.triggered.connect(lambda: self._prompt_merge_target(person_id))
        menu.exec(global_pos)

    def _build_merge_choices(self, source_person_id: str) -> list[tuple[str, str]]:
        return [
            (f"{self._display_names[summary.person_id]}（{summary.face_count} 张人脸）", summary.person_id)
            for summary in self._current_summaries
            if summary.person_id != source_person_id
        ]

    def _prompt_merge_target(self, source_person_id: str) -> None:
        if self._repository is None:
            return

        merge_choices = self._build_merge_choices(source_person_id)
        if not merge_choices:
            return

        labels = [label for label, _ in merge_choices]
        label_to_person_id = dict(merge_choices)
        selected_label, accepted = QInputDialog.getItem(
            self,
            "合并聚类",
            "选择要合并到的人物",
            labels,
            0,
            False,
        )
        if not accepted or selected_label not in label_to_person_id:
            return

        target_person_id = label_to_person_id[selected_label]
        try:
            self._repository.merge_persons(source_person_id, target_person_id)
        except Exception as exc:
            QMessageBox.critical(self, "Face Cluster MVP", str(exc))
            return

        self._status_label.setText(
            f"已将 {self._display_names.get(source_person_id, '当前人物')} 合并到 "
            f"{self._display_names.get(target_person_id, '目标人物')}。"
        )
        self._refresh_from_repository(target_person_id)

    def _clear_cards(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

    def _clear_table(self) -> None:
        self._table.setRowCount(0)
        self._detail_hint.setText("点击左侧圆形头像后，这里会展示对应聚类的人脸记录。")
