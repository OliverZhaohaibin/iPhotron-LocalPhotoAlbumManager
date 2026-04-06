"""Floating window that displays EXIF metadata for the selected asset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional

from PySide6.QtCore import QDateTime, QEvent, QLocale, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import load_icon
from .main_window_metrics import TITLE_BAR_HEIGHT, WINDOW_CONTROL_BUTTON_SIZE, WINDOW_CONTROL_GLYPH_SIZE


@dataclass
class _FormattedMetadata:
    """Pre-formatted strings used to populate the info panel labels."""

    name: str = ""
    timestamp: str = ""
    camera: str = ""
    lens: str = ""
    summary: str = ""
    exposure_line: str = ""
    is_video: bool = False


class InfoPanel(QWidget):
    """Small helper window that mirrors macOS Photos' info popover.

    The panel uses a frameless rounded window with a custom title bar
    whose close button reuses the main window's ``red.close.circle.svg``
    glyph for visual consistency.
    """

    _CORNER_RADIUS = 12.0
    _SHADOW_SIZE = 16
    _SHADOW_MAX_ALPHA = 18
    _SHADOW_RADIUS_GROWTH = 0.5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(320)

        self._metadata: Optional[dict[str, Any]] = None
        self._current_rel: Optional[str] = None
        self._drag_active = False
        self._drag_offset = None
        self._centered = False

        # -- title bar -----------------------------------------------------
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(TITLE_BAR_HEIGHT)
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(16, 10, 12, 6)
        title_layout.setSpacing(8)

        self._title_label = QLabel("Info", self._title_bar)
        self._title_label.setObjectName("infoPanelTitleLabel")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        )
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_layout.addWidget(self._title_label, 1)

        self._close_button = QToolButton(self._title_bar)
        self._close_button.setIcon(load_icon("red.close.circle.svg"))
        self._close_button.setIconSize(WINDOW_CONTROL_GLYPH_SIZE)
        self._close_button.setFixedSize(WINDOW_CONTROL_BUTTON_SIZE)
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._close_button.setToolTip("Close")
        self._apply_close_button_style()
        self._close_button.clicked.connect(self.close)
        title_layout.addWidget(
            self._close_button, 0, Qt.AlignmentFlag.AlignRight,
        )

        # -- content labels ------------------------------------------------
        self._filename_label = QLabel()
        self._filename_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._filename_label.setWordWrap(True)

        self._timestamp_label = QLabel()
        self._timestamp_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._timestamp_label.setWordWrap(True)

        self._camera_label = QLabel()
        self._camera_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._camera_label.setWordWrap(True)

        self._lens_label = QLabel()
        self._lens_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lens_label.setWordWrap(True)

        self._summary_label = QLabel()
        self._summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._summary_label.setWordWrap(True)

        self._exposure_label = QLabel()
        self._exposure_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._exposure_label.setWordWrap(True)

        # -- root layout ---------------------------------------------------
        s = self._SHADOW_SIZE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, s, s)
        layout.setSpacing(0)
        layout.addWidget(self._title_bar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 8, 16, 16)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._filename_label)
        content_layout.addWidget(self._timestamp_label)

        metadata_frame = QWidget(self)
        metadata_layout = QVBoxLayout(metadata_frame)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(6)
        metadata_layout.addWidget(self._camera_label)
        metadata_layout.addWidget(self._lens_label)
        metadata_layout.addWidget(self._summary_label)
        content_layout.addWidget(metadata_frame)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(separator)

        exposure_container = QWidget(self)
        exposure_layout = QHBoxLayout(exposure_container)
        exposure_layout.setContentsMargins(0, 0, 0, 0)
        exposure_layout.addWidget(self._exposure_label)
        content_layout.addWidget(exposure_container)

        content_layout.addStretch(1)
        layout.addWidget(content, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_asset_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Populate the panel with information extracted from *metadata*."""

        self._metadata = dict(metadata)
        self._current_rel = str(metadata.get("rel") or metadata.get("name") or "") or None

        formatted = self._format_metadata(metadata)
        self._filename_label.setText(formatted.name)
        self._timestamp_label.setText(formatted.timestamp)
        self._camera_label.setVisible(bool(formatted.camera))
        self._camera_label.setText(formatted.camera)
        self._lens_label.setVisible(bool(formatted.lens))
        self._lens_label.setText(formatted.lens)
        self._summary_label.setVisible(bool(formatted.summary))
        self._summary_label.setText(formatted.summary)
        if formatted.exposure_line:
            self._exposure_label.setText(formatted.exposure_line)
        else:
            fallback = (
                "Detailed video information is unavailable."
                if formatted.is_video
                else "Detailed exposure information is unavailable."
            )
            self._exposure_label.setText(fallback)

    def clear(self) -> None:
        """Reset the panel to an empty state without hiding the window."""

        self._metadata = None
        self._current_rel = None
        for label in (
            self._filename_label,
            self._timestamp_label,
            self._camera_label,
            self._lens_label,
            self._summary_label,
            self._exposure_label,
        ):
            label.clear()
        self._exposure_label.setText("No metadata available for this item.")

    def current_rel(self) -> Optional[str]:
        """Return the relative path associated with the displayed asset."""

        return self._current_rel

    @property
    def close_button(self) -> QToolButton:
        """Expose the close button for external signal wiring."""

        return self._close_button

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_close_button_style(self) -> None:
        """Recompute hover/pressed colours from the current palette."""
        text = self.palette().color(QPalette.ColorRole.WindowText)
        hover = QColor(text)
        hover.setAlpha(20)
        pressed = QColor(text)
        pressed.setAlpha(35)
        self._close_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; }"
            f"QToolButton:hover {{ background-color: {hover.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
            f"QToolButton:pressed {{ background-color: {pressed.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
        )

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_close_button_style()
        super().changeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        """Centre the panel over its parent window the first time it appears."""

        super().showEvent(event)
        if not self._centered:
            self._centered = True
            parent = self.parentWidget()
            if parent is not None and parent.isVisible():
                center = parent.geometry().center()
                hint = self.sizeHint()
                self.move(
                    center.x() - hint.width() // 2,
                    center.y() - hint.height() // 2,
                )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Draw drop shadow and an anti-aliased rounded rectangle."""

        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        s = self._SHADOW_SIZE
        content_rect = QRectF(self.rect()).adjusted(0, 0, -s, -s)
        radius = min(
            self._CORNER_RADIUS,
            min(content_rect.width(), content_rect.height()) / 2.0,
        )

        # -- drop shadow (right + bottom only) -----------------------------
        shadow_steps = s
        for i in range(shadow_steps):
            alpha = int(self._SHADOW_MAX_ALPHA * (1 - i / shadow_steps) ** 2)
            if alpha <= 0:
                continue
            shadow_color = QColor(0, 0, 0, alpha)
            spread = float(i)
            shadow_rect = content_rect.adjusted(spread, spread, spread, spread)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(
                shadow_rect,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(shadow_path, shadow_color)

        # -- background ----------------------------------------------------
        path = QPainterPath()
        path.addRoundedRect(content_rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        bg_color = self.palette().color(QPalette.ColorRole.Window)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, bg_color)

        border_color = self.palette().color(QPalette.ColorRole.Mid)
        border_color.setAlpha(80)
        painter.setPen(border_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Begin a drag when clicking on the title bar area."""

        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._title_bar.geometry().contains(local_pos):
                self._drag_active = True
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Move the panel when dragging the title bar."""

        if self._drag_active:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._drag_active = False
                self._drag_offset = None
                return

            if self._drag_offset is not None:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.move(new_pos)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """End a title-bar drag."""

        if self._drag_active:
            self._drag_active = False
            self._drag_offset = None
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _format_metadata(self, metadata: Mapping[str, Any]) -> _FormattedMetadata:
        """Return a :class:`_FormattedMetadata` snapshot for *metadata*."""

        info = dict(metadata)
        name = self._resolve_name(info)
        timestamp = self._format_timestamp(info.get("dt"))
        camera = self._format_camera(info)
        lens = self._format_lens(info)
        is_video = bool(info.get("is_video"))
        summary = (
            self._format_video_summary(info)
            if is_video
            else self._format_photo_summary(info)
        )
        exposure_line = (
            self._format_video_details(info)
            if is_video
            else self._format_exposure_line(info)
        )
        return _FormattedMetadata(
            name=name,
            timestamp=timestamp,
            camera=camera,
            lens=lens,
            summary=summary,
            exposure_line=exposure_line,
            is_video=is_video,
        )

    def _resolve_name(self, info: Mapping[str, Any]) -> str:
        """Return a human readable filename from *info*."""

        name = info.get("name")
        if isinstance(name, str) and name:
            return name
        rel = info.get("rel")
        if isinstance(rel, str) and rel:
            return Path(rel).name
        abs_path = info.get("abs")
        if isinstance(abs_path, str) and abs_path:
            return Path(abs_path).name
        return ""

    def _format_timestamp(self, value: Any) -> str:
        """Return *value* formatted using the current locale."""

        if not isinstance(value, str) or not value:
            return ""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
        localized = parsed.astimezone()
        qt_datetime = QDateTime(localized)
        formatted = QLocale.system().toString(qt_datetime, QLocale.FormatType.LongFormat)
        if formatted:
            return formatted
        return localized.strftime("%Y-%m-%d %H:%M:%S")

    def _format_camera(self, info: Mapping[str, Any]) -> str:
        """Combine camera make and model if they are available."""

        make = info.get("make") if isinstance(info.get("make"), str) else None
        model = info.get("model") if isinstance(info.get("model"), str) else None
        if make and model:
            if model.lower().startswith(make.lower()):
                return model
            return f"{make} {model}"
        if model:
            return model
        if make:
            return make
        return ""

    def _format_lens(self, info: Mapping[str, Any]) -> str:
        """Return the lens description augmented with focal and aperture data."""

        lens = info.get("lens") if isinstance(info.get("lens"), str) else None
        focal_text = self._format_focal_length(info.get("focal_length"))
        aperture_text = self._format_aperture(info.get("f_number"))
        components = [component for component in (focal_text, aperture_text) if component]
        # If the lens string already encodes focal-length info (e.g. a LensInfo spec
        # string like "23mm f/2"), do not append the separate focal/aperture fields —
        # they would merely duplicate values already present in the lens string.
        if lens and "mm" in lens:
            return lens
        if lens and components:
            return f"{lens} — {' '.join(components)}"
        if lens:
            return lens
        if components:
            return " ".join(components)
        return ""

    def _format_photo_summary(self, info: Mapping[str, Any]) -> str:
        """Compose a single line summarising the image dimensions and size."""

        width = info.get("w")
        height = info.get("h")
        dimensions = ""
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            dimensions = f"{width} × {height}"

        size_text = self._format_filesize(info.get("bytes"))
        format_text = self._format_format(info)
        parts = [part for part in (dimensions, size_text, format_text) if part]
        return "    ".join(parts)

    def _format_video_summary(self, info: Mapping[str, Any]) -> str:
        """Summarise a video's dimensions, size, and codec in a single line."""

        width = info.get("w")
        height = info.get("h")
        dimensions = ""
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            dimensions = f"{width} × {height}"

        size_text = self._format_filesize(info.get("bytes"))
        codec_text = self._format_codec(info)
        parts = [part for part in (dimensions, size_text, codec_text) if part]
        return "    ".join(parts)

    def _format_exposure_line(self, info: Mapping[str, Any]) -> str:
        """Compose the ISO, focal length, EV, aperture, and shutter speed line."""

        iso_value = info.get("iso")
        iso_text = ""
        if isinstance(iso_value, (int, float)):
            iso_text = f"ISO {int(round(float(iso_value)))}"

        focal_text = self._format_focal_length(info.get("focal_length"))
        ev_text = self._format_exposure_comp(info.get("exposure_compensation"))
        aperture_text = self._format_aperture(info.get("f_number"))
        shutter_text = self._format_shutter(info.get("exposure_time"))

        parts = [part for part in (iso_text, focal_text, ev_text, aperture_text, shutter_text) if part]
        return "    ".join(parts)

    def _format_video_details(self, info: Mapping[str, Any]) -> str:
        """Compose the frame-rate and duration line for a video asset."""

        frame_rate_text = self._format_frame_rate(info.get("frame_rate"))
        duration_text = self._format_duration(info.get("dur"))
        codec_summary = self._format_codec(info)
        codec_text = ""
        # Show the codec twice only when the summary had no value; this keeps
        # the layout tidy while still surfacing the information somewhere.
        if not codec_summary:
            codec_text = self._format_format(info)

        parts = [part for part in (frame_rate_text, duration_text, codec_text) if part]
        return "    ".join(parts)

    def _format_focal_length(self, value: Any) -> str:
        """Return a formatted focal length string in millimetres."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        if abs(numeric - round(numeric)) < 0.05:
            return f"{int(round(numeric))} mm"
        return f"{numeric:.1f} mm"

    def _format_aperture(self, value: Any) -> str:
        """Return a formatted aperture string (ƒ-number)."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        return f"ƒ{self._format_decimal(numeric, precision=2)}"

    def _format_exposure_comp(self, value: Any) -> str:
        """Return exposure compensation in EV when available."""

        numeric = self._coerce_float(value)
        if numeric is None:
            return ""
        text = self._format_decimal(numeric, precision=2)
        return f"{text} ev"

    def _format_shutter(self, value: Any) -> str:
        """Return shutter speed formatted as a fraction when suitable."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        if numeric >= 1:
            return f"{self._format_decimal(numeric, precision=2)} s"
        fraction = Fraction(numeric).limit_denominator(8000)
        approx = fraction.numerator / fraction.denominator
        if abs(approx - numeric) <= 1e-4:
            if fraction.numerator == 1:
                return f"1/{fraction.denominator} s"
            return f"{fraction.numerator}/{fraction.denominator} s"
        return f"{self._format_decimal(numeric, precision=4)} s"

    def _format_filesize(self, value: Any) -> str:
        """Return *value* expressed in human readable units."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(numeric)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"

        rounded = round(size, 1)
        if float(rounded).is_integer():
            return f"{int(rounded)} {units[unit_index]}"
        return f"{rounded:.1f} {units[unit_index]}"

    def _format_codec(self, info: Mapping[str, Any]) -> str:
        """Return a readable codec label derived from the stored metadata."""

        codec_value = info.get("codec")
        if isinstance(codec_value, str):
            candidate = codec_value.strip()
            if not candidate:
                return ""
            if "," in candidate:
                candidate = candidate.split(",", 1)[0].strip()
            if "/" in candidate:
                candidate = candidate.split("/")[-1].strip()
            if "(" in candidate:
                candidate = candidate.split("(")[0].strip()
            normalized = candidate.replace(".", "").replace("-", "").replace(" ", "").upper()
            mapping = {
                "H264": "H.264",
                "AVC": "H.264",
                "AVC1": "H.264",
                "H265": "H.265",
                "HEVC": "HEVC",
                "X265": "H.265",
                "PRORES": "ProRes",
            }
            if normalized in mapping:
                return mapping[normalized]
            if candidate.isupper():
                return candidate
            if candidate.islower():
                return candidate.upper()
            return candidate
        return self._format_format(info)

    def _format_frame_rate(self, value: Any) -> str:
        """Return the frame-rate with two decimal places when available."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        return f"{self._format_decimal(numeric, precision=2)} fps"

    def _format_duration(self, value: Any) -> str:
        """Return a short ``mm:ss`` or ``hh:mm:ss`` string for *value* seconds."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric < 0:
            return ""
        total_seconds = int(round(numeric))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:d}:{seconds:02d}"

    def _format_format(self, info: Mapping[str, Any]) -> str:
        """Return a short label describing the image format."""

        name = info.get("name") if isinstance(info.get("name"), str) else None
        if name:
            suffix = Path(name).suffix
            if suffix:
                extension = suffix.lstrip(".")
                if extension.lower() in {"heic", "heif"}:
                    return "HEIF"
                return extension.upper()
        mime = info.get("mime") if isinstance(info.get("mime"), str) else None
        if mime:
            subtype = mime.split("/")[-1]
            if subtype.lower() in {"heic", "heif"}:
                return "HEIF"
            return subtype.upper()
        return ""

    def _format_decimal(self, value: float, *, precision: int) -> str:
        """Return *value* formatted with ``precision`` decimal places."""

        text = f"{value:.{precision}f}"
        text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _coerce_float(self, value: Any) -> Optional[float]:
        """Return *value* as ``float`` when it represents a numeric quantity."""

        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value)
            except ValueError:
                return None
        return None
