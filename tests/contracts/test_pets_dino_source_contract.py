from __future__ import annotations

import json
import os
from urllib import request

import pytest

from iPhoto.pets import pipeline as pet_pipeline


pytestmark = pytest.mark.skipif(
    os.environ.get("IPHOTO_RUN_PETS_DINO_SOURCE_CONTRACT") != "1",
    reason="real DINOv2 source contract is opt-in",
)


def test_real_pinned_dinov2_source_imports_and_traces() -> None:
    torch = pytest.importorskip("torch")
    manifest = pet_pipeline._EMBEDDER_MANIFEST
    repository = str(manifest["source_repository"])
    revision = str(manifest["source_revision"])

    api_request = request.Request(
        f"https://api.github.com/repos/{repository}/git/commits/{revision}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "iPhotron-CI"},
    )
    with request.urlopen(api_request, timeout=30) as response:  # noqa: S310
        commit = json.load(response)

    assert commit["sha"] == revision
    assert commit["tree"]["sha"] == manifest["source_tree_sha1"]

    source = f"{repository}:{revision}"
    model = torch.hub.load(
        source,
        str(manifest["model_name"]),
        source="github",
        trust_repo=True,
        # PyTorch's validation endpoint only understands branches/tags.  The
        # runtime instead requires an exact 40-char commit and CI verifies the
        # commit's Git tree above.
        skip_validation=True,
        pretrained=False,
    ).eval().cpu()

    torch.manual_seed(0)
    example = torch.randn(tuple(manifest["input_shape"]), dtype=torch.float32)
    with torch.no_grad():
        eager = model(example)
        traced = torch.jit.trace(model, example, strict=False).eval()
        scripted = traced(example)

    assert tuple(eager.shape) == tuple(manifest["output_shape"])
    assert tuple(scripted.shape) == tuple(manifest["output_shape"])
    torch.testing.assert_close(scripted, eager, rtol=1e-4, atol=1e-5)
