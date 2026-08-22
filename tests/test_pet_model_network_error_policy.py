from __future__ import annotations

import errno
from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline


def test_network_permission_error_does_not_trigger_storage_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = tmp_path / "extension"
    cache = tmp_path / "cache"
    target = bundled / "detector" / "yolox_nano_coco.onnx"
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    calls: list[str] = []

    def fail_urlopen(url: str, **_kwargs):
        calls.append(url)
        raise PermissionError(errno.EACCES, "network denied")

    monkeypatch.setattr(pet_pipeline.request, "urlopen", fail_urlopen)

    with pytest.raises(RuntimeError, match="failed to download") as raised:
        pet_pipeline.ensure_pet_detector_model(target)

    assert not isinstance(raised.value, pet_pipeline._ModelStoragePermissionError)
    assert len(calls) == 1
    assert not cache.exists()
