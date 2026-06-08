"""Section registration and factory for the edit sidebar."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QFrame, QToolButton, QWidget

from iPhoto.gui.i18n import tr

from ..icon import load_icon
from ..palette import Edit_SIDEBAR_FONT
from .collapsible_section import CollapsibleSection
from .edit_bw_section import EditBWSection
from .edit_color_section import EditColorSection
from .edit_curve_section import EditCurveSection
from .edit_definition_section import EditDefinitionSection
from .edit_denoise_section import EditDenoiseSection
from .edit_levels_section import EditLevelsSection
from .edit_light_section import EditLightSection
from .edit_selective_color_section import EditSelectiveColorSection
from .edit_sharpen_section import EditSharpenSection
from .edit_vignette_section import EditVignetteSection
from .edit_wb_section import EditWBSection


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
        "definition", "Definition", "definition.svg", EditDefinitionSection, collapsed=True, icon_scale=1.25,
    ),
    SectionConfig(
        "selective_color",
        "Selective Color",
        "selectivecolor.svg",
        EditSelectiveColorSection,
        collapsed=True,
        icon_scale=0.95,
    ),
    SectionConfig(
        "denoise",
        "Noise Reduction",
        "denoise.svg",
        EditDenoiseSection,
        collapsed=True,
        icon_scale=1.20,
    ),
    SectionConfig(
        "sharpen",
        "Sharpen",
        "sharpen.svg",
        EditSharpenSection,
        collapsed=True,
        icon_scale=1.20,
    ),
    SectionConfig(
        "vignette",
        "Vignette",
        "vignette.svg",
        EditVignetteSection,
        collapsed=True,
        icon_scale=1.20,
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
            _section_title(config.title),
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
        reset_button.setToolTip(_reset_tooltip(config.title))

        toggle_button = QToolButton(container)
        toggle_button.setAutoRaise(True)
        toggle_button.setCheckable(True)
        toggle_button.setIcon(load_icon("circle.svg"))
        toggle_button.setToolTip(_toggle_tooltip(config.title))

        container.add_header_control(reset_button)
        container.add_header_control(toggle_button)

        bundle = SectionBundle(section, container, reset_button, toggle_button)
        self.bundles[config.key] = bundle
        return bundle

    def retranslate_ui(self) -> None:
        """Refresh section titles and header controls after a language change."""

        for config in SECTION_CONFIGS:
            bundle = self.bundles.get(config.key)
            if bundle is None:
                continue
            bundle.container.set_title(_section_title(config.title))
            bundle.reset_button.setToolTip(_reset_tooltip(config.title))
            bundle.toggle_button.setToolTip(_toggle_tooltip(config.title))
            method = getattr(bundle.section, "retranslate_ui", None)
            if callable(method):
                method()

    @staticmethod
    def build_separator(parent: QWidget) -> QFrame:
        """Return a subtle divider separating adjacent section headers."""

        separator = QFrame(parent)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setStyleSheet("QFrame { background-color: palette(mid); }")
        separator.setFixedHeight(1)
        return separator


def _section_title(source_text: str) -> str:
    if source_text == "Light":
        return tr("EditSidebar", "Light")
    if source_text == "Color":
        return tr("EditSidebar", "Color")
    if source_text == "Black & White":
        return tr("EditSidebar", "Black & White")
    if source_text == "White Balance":
        return tr("EditSidebar", "White Balance")
    if source_text == "Curve":
        return tr("EditSidebar", "Curve")
    if source_text == "Levels":
        return tr("EditSidebar", "Levels")
    if source_text == "Definition":
        return tr("EditSidebar", "Definition")
    if source_text == "Selective Color":
        return tr("EditSidebar", "Selective Color")
    if source_text == "Noise Reduction":
        return tr("EditSidebar", "Noise Reduction")
    if source_text == "Sharpen":
        return tr("EditSidebar", "Sharpen")
    if source_text == "Vignette":
        return tr("EditSidebar", "Vignette")
    return source_text


def _reset_tooltip(section_title: str) -> str:
    return tr("EditSidebar", "Reset {section} adjustments").format(
        section=_section_title(section_title)
    )


def _toggle_tooltip(section_title: str) -> str:
    return tr("EditSidebar", "Toggle {section} adjustments").format(
        section=_section_title(section_title)
    )
