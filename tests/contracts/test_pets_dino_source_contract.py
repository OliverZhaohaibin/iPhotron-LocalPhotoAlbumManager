from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib import request

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
    with request.urlopen(str(manifest["torchscript_url"]), timeout=60) as response:  # noqa: S310
        target.write_bytes(response.read(int(manifest["torchscript_size"]) + 1))
    assert target.stat().st_size == int(manifest["torchscript_size"])
    assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest["torchscript_sha256"]

    model = torch.jit.load(str(target), map_location="cpu").eval()
    example = torch.zeros(tuple(manifest["input_shape"]), dtype=torch.float32)
    with torch.no_grad():
        output = model(example)
    assert tuple(output.shape) == tuple(manifest["output_shape"])
