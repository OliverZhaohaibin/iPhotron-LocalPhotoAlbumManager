"""Real ONNX inference and legacy-space comparison, without importing PyTorch."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from iPhoto.pets.model_contract import PET_MODEL_MANIFEST
from iPhoto.pets.pipeline import _cluster_embeddings_bounded_single_link, _DinoV2Embedder

pytestmark = pytest.mark.dinov2_model_contract


def test_verified_dinov2_cpu_contract_and_legacy_embeddings():
    if os.environ.get("IPHOTO_RUN_DINOV2_MODEL_CONTRACT") != "1":
        pytest.skip("real DINOv2 contract runs in the ONNX CPU matrix")
    root = Path(os.environ["IPHOTO_DINOV2_CONTRACT_DIR"])
    manifest = PET_MODEL_MANIFEST["embedder"]
    path = root / "dinov2_vits14.onnx"
    assert path.stat().st_size == manifest["onnx_size"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["onnx_sha256"]
    model = _DinoV2Embedder(
        root,
        model_name="dinov2_vits14",
        allow_model_download=False,
        execution_providers=["CPUExecutionProvider"],
    )
    try:
        reference = np.load(root / "torchscript-reference.npz", allow_pickle=False)
        old = reference["raw_outputs"][:, 0, :]
        new = np.stack(
            [
                model._session.run(["embedding"], {"image": tensor})[0][0]
                for tensor in reference["inputs"]
            ]
        )
        assert new.dtype == np.float32 and new.shape == old.shape and new.shape[1] == 384
        assert np.isfinite(new).all()
        np.testing.assert_allclose(new, old, rtol=2e-4, atol=2e-4)
        old = old / np.linalg.norm(old, axis=1, keepdims=True)
        new = new / np.linalg.norm(new, axis=1, keepdims=True)
        assert np.min(np.sum(old.astype(np.float64) * new.astype(np.float64), axis=1)) >= 0.9999
        np.testing.assert_allclose(old @ old.T, new @ new.T, rtol=0, atol=1e-5)
        for threshold in (0.42, 0.63):
            kwargs = {"compatibility_keys": ["cat"] * len(old), "distance_threshold": threshold}
            before = _cluster_embeddings_bounded_single_link(old, **kwargs)
            after = _cluster_embeddings_bounded_single_link(new, **kwargs)
            assert np.array_equal(before[:, None] == before, after[:, None] == after)
        assert model._session.get_providers() == ["CPUExecutionProvider"]
    finally:
        model._runtime.close()
