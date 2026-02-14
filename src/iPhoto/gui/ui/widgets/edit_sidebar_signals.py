"""Signal routing between edit sections and the sidebar."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .edit_sidebar import EditSidebar
    from .edit_sidebar_sections import EditSectionRegistry
    from .edit_perspective_controls import PerspectiveControls


class EditSignalRouter:
    """Wires section-level signals to sidebar-level relay signals."""

    @staticmethod
    def connect_section_signals(
        sidebar: EditSidebar,
        registry: EditSectionRegistry,
        perspective_controls: PerspectiveControls,
    ) -> None:
        """Connect all relay signals from individual sections to the sidebar."""

        bundles = registry.bundles

        # Every section relays interaction start / finish to the sidebar.
        for bundle in bundles.values():
            bundle.section.interactionStarted.connect(sidebar.interactionStarted)
            bundle.section.interactionFinished.connect(sidebar.interactionFinished)

        # BW-specific relays
        bw = bundles["bw"].section
        bw.paramsPreviewed.connect(sidebar.bwParamsPreviewed)
        bw.paramsCommitted.connect(sidebar.bwParamsCommitted)

        # WB-specific relays
        wb = bundles["wb"].section
        wb.wbParamsPreviewed.connect(sidebar.wbParamsPreviewed)
        wb.wbParamsCommitted.connect(sidebar.wbParamsCommitted)
        wb.eyedropperModeChanged.connect(sidebar.wbEyedropperModeChanged)

        # Curve-specific relays
        curve = bundles["curve"].section
        curve.curveParamsPreviewed.connect(sidebar.curveParamsPreviewed)
        curve.curveParamsCommitted.connect(sidebar.curveParamsCommitted)
        curve.eyedropperModeChanged.connect(sidebar.curveEyedropperModeChanged)

        # Levels-specific relays
        levels = bundles["levels"].section
        levels.levelsParamsPreviewed.connect(sidebar.levelsParamsPreviewed)
        levels.levelsParamsCommitted.connect(sidebar.levelsParamsCommitted)

        # Selective Color-specific relays
        sc = bundles["selective_color"].section
        sc.selectiveColorParamsPreviewed.connect(sidebar.selectiveColorParamsPreviewed)
        sc.selectiveColorParamsCommitted.connect(sidebar.selectiveColorParamsCommitted)
        sc.eyedropperModeChanged.connect(sidebar.selectiveColorEyedropperModeChanged)

        # Perspective controls
        perspective_controls.interactionStarted.connect(sidebar.perspectiveInteractionStarted)
        perspective_controls.interactionFinished.connect(sidebar.perspectiveInteractionFinished)
        perspective_controls.interactionStarted.connect(sidebar.interactionStarted)
        perspective_controls.interactionFinished.connect(sidebar.interactionFinished)
