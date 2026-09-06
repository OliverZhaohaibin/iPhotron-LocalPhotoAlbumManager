from __future__ import annotations

from iPhoto.gui.ui import icons


def test_plain_svg_icon_uses_explicit_renderer_not_path_constructor(monkeypatch, qapp) -> None:
    constructor_args: list[tuple[object, ...]] = []

    class RecordingIcon:
        def __init__(self, *args: object) -> None:
            constructor_args.append(args)

        def addPixmap(self, _pixmap) -> None:  # noqa: N802 - Qt-compatible test double
            return None

    icons._ICON_CACHE.clear()
    monkeypatch.setattr(icons, "QIcon", RecordingIcon)

    try:
        icon = icons.load_icon("red.close.circle.svg")

        assert isinstance(icon, RecordingIcon)
        assert constructor_args == [()]
    finally:
        icons._ICON_CACHE.clear()
