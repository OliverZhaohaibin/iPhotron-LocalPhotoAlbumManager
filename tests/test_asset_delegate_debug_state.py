import pytest

pytest.importorskip("PySide6")

from iPhoto.gui.ui.widgets.asset_delegate import AssetGridDelegate


class _ExplosiveRepr:
    def __repr__(self):
        raise RuntimeError("boom")


def test_state_debug_repr_handles_bad_repr() -> None:
    value = AssetGridDelegate._state_debug_repr(_ExplosiveRepr())
    assert value == "<_ExplosiveRepr>"
