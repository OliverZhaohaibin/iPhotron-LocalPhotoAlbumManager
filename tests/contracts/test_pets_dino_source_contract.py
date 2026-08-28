from __future__ import annotations

import os
from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline


pytestmark = pytest.mark.skipif(
    os.environ.get("IPHOTO_RUN_PETS_DINO_RELEASE_CONTRACT") != "1",
    reason="real DINOv2 Release contract is opt-in",
)


def test_real_pinned_dinov2_release_downloads_and_loads(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    manifest = pet_pipeline._EMBEDDER_MANIFEST
    target = tmp_path / "dinov2_vits14.pt"
    pet_pipeline._download_file(
        str(manifest["torchscript_url"]),
        target,
        label="DINOv2 TorchScript Release artifact",
        expected_sha256=str(manifest["torchscript_sha256"]),
        max_bytes=int(manifest["torchscript_size"]),
        exact_size=int(manifest["torchscript_size"]),
    )

    model = torch.jit.load(str(target), map_location="cpu").eval()
    example = torch.zeros(tuple(manifest["input_shape"]), dtype=torch.float32)
    with torch.no_grad():
        output = model(example)
    assert tuple(output.shape) == tuple(manifest["output_shape"])
