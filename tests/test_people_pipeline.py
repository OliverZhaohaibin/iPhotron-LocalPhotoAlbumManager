from __future__ import annotations

import os
import sys
from types import ModuleType
from pathlib import Path

from iPhoto.people.pipeline import FaceClusterPipeline


def test_face_pipeline_uses_shared_model_root(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class FakeFaceAnalysis:
        def __init__(self, *, name: str, root: str, providers: list[str]) -> None:
            calls["name"] = name
            calls["root"] = root
            calls["providers"] = providers

        def prepare(self, *, ctx_id: int, det_size: tuple[int, int]) -> None:
            calls["ctx_id"] = ctx_id
            calls["det_size"] = det_size

    insightface_module = ModuleType("insightface")
    app_module = ModuleType("insightface.app")
    app_module.FaceAnalysis = FakeFaceAnalysis
    insightface_module.app = app_module

    monkeypatch.setitem(sys.modules, "insightface", insightface_module)
    monkeypatch.setitem(sys.modules, "insightface.app", app_module)
    monkeypatch.setattr("iPhoto.people.pipeline._patch_insightface_alignment_estimate", lambda: None)
    monkeypatch.setattr(
        "iPhoto.people.pipeline._resolve_execution_providers",
        lambda: ["CPUExecutionProvider"],
    )

    monkeypatch.setenv("INSIGHTFACE_HOME", str(tmp_path / "legacy-cache"))

    model_root = tmp_path / "extension" / "models"
    pipeline = FaceClusterPipeline(model_root=model_root)

    app = pipeline._ensure_face_analysis()

    assert app is pipeline._ensure_face_analysis()
    assert model_root.is_dir()
    assert calls == {
        "name": "buffalo_s",
        "root": str((tmp_path / "extension").resolve()),
        "providers": ["CPUExecutionProvider"],
        "ctx_id": -1,
        "det_size": (640, 640),
    }
    assert os.environ["INSIGHTFACE_HOME"] == str((tmp_path / "extension").resolve())
