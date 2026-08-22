#!/usr/bin/env python3
"""Build and verify a DINOv2 TorchScript cache from Meta's official checkpoint.

The generated TorchScript SHA-256 is reported for diagnostics only. It is not
part of the runtime identity contract because serialization may vary between
supported PyTorch versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import torch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "src" / "iPhoto" / "pets" / "model_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="TorchScript cache destination")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["embedder"]
    repository = str(manifest["source_repository"])
    revision = str(manifest["source_revision"])
    model_name = str(manifest["model_name"])
    weights_url = str(manifest["weights_url"])
    source = f"{repository}:{revision}"

    torch.manual_seed(0)
    example = torch.randn(tuple(manifest["input_shape"]), dtype=torch.float32)
    model = torch.hub.load(
        source,
        model_name,
        source="github",
        trust_repo=True,
        weights=weights_url,
    ).eval().cpu()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iphoto-dinov2-cache-") as temp_dir:
        candidate = Path(temp_dir) / args.output.name
        with torch.no_grad():
            eager_output = model(example)
            traced = torch.jit.trace(model, example, strict=False)
            traced.save(str(candidate))
            scripted = torch.jit.load(str(candidate), map_location="cpu").eval()
            scripted_output = scripted(example)
        if isinstance(eager_output, (list, tuple)):
            eager_output = eager_output[0]
        if isinstance(scripted_output, (list, tuple)):
            scripted_output = scripted_output[0]
        expected_shape = tuple(manifest["output_shape"])
        if tuple(scripted_output.shape) != expected_shape:
            raise RuntimeError(
                f"output shape mismatch: {tuple(scripted_output.shape)} != {expected_shape}"
            )
        torch.testing.assert_close(scripted_output, eager_output, rtol=1e-4, atol=1e-5)
        artifact_sha256 = _sha256(candidate)
        artifact_size = candidate.stat().st_size
        candidate.replace(args.output)

    print(
        json.dumps(
            {
                "path": str(args.output),
                "sha256": artifact_sha256,
                "size": artifact_size,
                "source_revision": revision,
                "weights_url": weights_url,
                "numeric_equivalence": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
