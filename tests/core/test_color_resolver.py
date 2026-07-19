from __future__ import annotations

import builtins

from PySide6.QtGui import QImage

from iPhoto.core.color_resolver import compute_color_statistics


def test_color_statistics_does_not_import_numba(monkeypatch) -> None:
    imported: list[str] = []
    original_import = builtins.__import__

    def recording_import(name, *args, **kwargs):
        imported.append(str(name))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", recording_import)
    image = QImage(16, 16, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)

    compute_color_statistics(image)

    assert "numba" not in imported
