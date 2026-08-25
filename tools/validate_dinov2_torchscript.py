#!/usr/bin/env python3
"""Validate one fixed DINOv2 TorchScript artifact without source code."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import torch


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    digest = _sha256(args.artifact)
    size = args.artifact.stat().st_size
    if digest != metadata["torchscript_sha256"] or size != metadata["torchscript_size"]:
        raise RuntimeError("TorchScript artifact does not match its release metadata")
    runtime_version = str(torch.__version__).split("+", 1)[0]
    if runtime_version != metadata["producer_torch_version"]:
        raise RuntimeError(
            f"torch runtime mismatch: {runtime_version} != {metadata['producer_torch_version']}"
        )

    model = torch.jit.load(str(args.artifact), map_location="cpu").eval()
    example = torch.zeros(tuple(metadata["input_shape"]), dtype=torch.float32)
    with torch.no_grad():
        output = model(example)
    if isinstance(output, (list, tuple)):
        output = output[0]
    if tuple(output.shape) != tuple(metadata["output_shape"]):
        raise RuntimeError(
            f"output shape mismatch: {tuple(output.shape)} != {tuple(metadata['output_shape'])}"
        )
    output_digest = hashlib.sha256(output.detach().cpu().contiguous().numpy().tobytes()).hexdigest()
    print(
        json.dumps(
            {
                "artifact_sha256": digest,
                "artifact_size": size,
                "output_sha256": output_digest,
                "output_shape": list(output.shape),
                "torch_version": runtime_version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
