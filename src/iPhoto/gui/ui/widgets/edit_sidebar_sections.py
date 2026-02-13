"""Section registration and factory for the edit sidebar."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QFrame, QToolButton, QWidget

from .collapsible_section import CollapsibleSection
from .edit_bw_section import EditBWSection
from .edit_color_section import EditColorSection
from .edit_curve_section import EditCurveSection
from .edit_levels_section import EditLevelsSection
from .edit_light_section import EditLightSection
from .edit_selective_color_section import EditSelectiveColorSection
from .edit_wb_section import EditWBSection
from ..palette import Edit_SIDEBAR_FONT
from ..icon import load_icon


@dataclass
class SectionConfig:
    """Describes how to build a single edit section."""

    key: str
    title: str
    icon: str
    section_class: type
    collapsed: bool = False
    icon_scale: float = 1.0


SECTION_CONFIGS: list[SectionConfig] = [
    SectionConfig("light", "Light", "sun.max.svg", EditLightSection, icon_scale=1.25),
    SectionConfig("color", "Color", "color.circle.svg", EditColorSection),
    SectionConfig(
        "bw", "Black & White", "circle.lefthalf.fill.svg", EditBWSection, icon_scale=1.1
    ),
    SectionConfig("wb", "White Balance", "whitebalance.square.svg", EditWBSection, collapsed=True),
    SectionConfig("curve", "Curve", "curve.svg", EditCurveSection, collapsed=True),
    SectionConfig("levels", "Levels", "level.square.svg", EditLevelsSection, collapsed=True),
    SectionConfig(
        "selective_color",
        "Selective Color",
        "selectivecolor.svg",
        EditSelectiveColorSection,
        collapsed=True,
        icon_scale=0.95,
    ),
]


@dataclass
class SectionBundle:
    """Holds a section widget, its collapsible container, and header buttons."""

    section: QWidget
    container: CollapsibleSection
    reset_button: QToolButton
    toggle_button: QToolButton


class EditSectionRegistry:
    """Creates and stores edit section bundles."""

    def __init__(self) -> None:
        self.bundles: dict[str, SectionBundle] = {}

    def create_section(self, config: SectionConfig, parent: QWidget) -> SectionBundle:
        """Instantiate a section widget, wrap it in a CollapsibleSection,
        and add reset / toggle header buttons."""

        section = config.section_class(parent)

        kwargs: dict = {"title_font": Edit_SIDEBAR_FONT}
        if config.icon_scale != 1.0:
            kwargs["icon_scale"] = config.icon_scale

        container = CollapsibleSection(
            config.title,
            config.icon,
            section,
            parent,
            **kwargs,
        )

        if config.collapsed:
            container.set_expanded(False)

        reset_button = QToolButton(container)
        reset_button.setAutoRaise(True)
        reset_button.setIcon(load_icon("arrow.uturn.left.svg"))
        reset_button.setToolTip(f"Reset {config.title} adjustments")

        toggle_button = QToolButton(container)
        toggle_button.setAutoRaise(True)
        toggle_button.setCheckable(True)
        toggle_button.setIcon(load_icon("circle.svg"))
        toggle_button.setToolTip(f"Toggle {config.title} adjustments")

        container.add_header_control(reset_button)
        container.add_header_control(toggle_button)

        bundle = SectionBundle(section, container, reset_button, toggle_button)
        self.bundles[config.key] = bundle
        return bundle

    @staticmethod
    def build_separator(parent: QWidget) -> QFrame:
        """Return a subtle divider separating adjacent section headers."""

        separator = QFrame(parent)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setStyleSheet("QFrame { background-color: palette(mid); }")
        separator.setFixedHeight(1)
        return separator
