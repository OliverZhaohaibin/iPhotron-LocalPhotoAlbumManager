from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import FaceClusterRepository, PersonSummary
from image_utils import render_round_pixmap
from worker import FaceClusterWorker, WorkerResult


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


class ClusterCard(QFrame):
    clicked = Signal(str)

    def __init__(self, summary: PersonSummary, parent=None) -> None:
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
        if summary.thumbnail_path and summary.thumbnail_path.exists():
            self._avatar_label.setPixmap(render_round_pixmap(summary.thumbnail_path, size=112))
        else:
            self._avatar_label.setText("无头像")

        self._title_label = QLabel(f"{summary.face_count} 张人脸")
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet("font-weight: 600;")

        self._subtitle_label = QLabel(summary.person_id[:8])
        self._subtitle_label.setAlignment(Qt.AlignCenter)
        self._subtitle_label.setStyleSheet("color: #6b7280;")

        layout.addWidget(self._avatar_label, alignment=Qt.AlignCenter)
        layout.addWidget(self._title_label)
        layout.addWidget(self._subtitle_label)
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_style()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._summary.person_id)
        super().mousePressEvent(event)

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
        self._repository = FaceClusterRepository(result.workspace.db_path)

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
        self._populate_cards(result.person_summaries)
        if result.person_summaries:
            self._select_person(result.person_summaries[0].person_id)

    def _populate_cards(self, summaries: list[PersonSummary]) -> None:
        self._clear_cards()
        if not summaries:
            self._empty_label.setText("没有可展示的聚类结果。")
            self._empty_label.show()
            return

        self._empty_label.hide()
        columns = 3
        for index, summary in enumerate(summaries):
            row = index // columns
            column = index % columns
            card = ClusterCard(summary)
            card.clicked.connect(self._select_person)
            self._grid_layout.addWidget(card, row, column)
            self._cards[summary.person_id] = card

        for column in range(columns):
            self._grid_layout.setColumnStretch(column, 1)

    def _select_person(self, person_id: str) -> None:
        if self._repository is None:
            return

        for current_id, card in self._cards.items():
            card.set_selected(current_id == person_id)

        rows = self._repository.get_faces_by_person(person_id)
        self._detail_hint.setText(
            f"当前聚类 {person_id[:8]}，共 {len(rows)} 条 faces 表记录。"
        )
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
