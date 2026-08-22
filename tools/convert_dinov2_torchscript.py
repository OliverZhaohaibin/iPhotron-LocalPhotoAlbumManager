#!/usr/bin/env python3
"""Build a DINOv2 TorchScript cache from Meta's official verified checkpoint.

Integrity verification applies to the downloaded upstream checkpoint only. The
generated TorchScript cache is validated semantically, but its serialized bytes
are not hash-pinned because they can vary across supported PyTorch versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from urllib import request
from urllib.parse import urlparse

import torch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "src" / "iPhoto" / "pets" / "model_manifest.json"
_DOWNLOAD_CHUNK_SIZE = 1024 * 256


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_verified_checkpoint(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    if urlparse(url).scheme.lower() != "https":
        raise RuntimeError("DINOv2 checkpoint URL must use HTTPS.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with request.urlopen(url, timeout=60) as response, destination.open("wb") as handle:
        total = 0
        while True:
            chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise RuntimeError("DINOv2 checkpoint exceeds its pinned size.")
            handle.write(chunk)

    actual_size = destination.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(
            f"DINOv2 checkpoint size mismatch: {actual_size} != {expected_size}"
        )
    actual_sha256 = _sha256(destination)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            "DINOv2 checkpoint SHA-256 mismatch: "
            f"{actual_sha256} != {expected_sha256}"
        )


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
    weights_sha256 = str(manifest["weights_sha256"])
    weights_size = int(manifest["weights_size"])
    source = f"{repository}:{revision}"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="iphoto-dinov2-cache-") as temp_dir:
        temp_root = Path(temp_dir)
        checkpoint = temp_root / "dinov2_vits14_pretrain.pth"
        candidate = temp_root / args.output.name

        _download_verified_checkpoint(
            weights_url,
            checkpoint,
            expected_sha256=weights_sha256,
            expected_size=weights_size,
        )

        model = torch.hub.load(
            source,
            model_name,
            source="github",
            trust_repo=True,
            weights=str(checkpoint),
        ).eval().cpu()

        torch.manual_seed(0)
        example = torch.randn(tuple(manifest["input_shape"]), dtype=torch.float32)
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
        derived_size = candidate.stat().st_size
        candidate.replace(args.output)

    print(
        json.dumps(
            {
                "path": str(args.output),
                "source_revision": revision,
                "weights_url": weights_url,
                "weights_sha256": weights_sha256,
                "weights_size": weights_size,
                "derived_torchscript_size": derived_size,
                "numeric_equivalence": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
