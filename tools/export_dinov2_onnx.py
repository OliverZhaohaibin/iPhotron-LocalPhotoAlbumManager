#!/usr/bin/env python3
"""Export the verified v1 DINOv2 artifact to a fixed FP32 ONNX graph.

Run only in the isolated environment in tools/model-dev/requirements.txt. The
input is the original release model, not a newly trained or Hub-selected model.
No application runtime imports this tool or executes TorchScript.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iPhoto.pets.image_utils import image_to_chw_float

SOURCE_REVISION = "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
SOURCE_SHA256 = "36e01591cfd52d194845d5b23b870dcb698f79c33f6140144180568f2155ab94"
SOURCE_SIZE = 88587286
OPSET = 17


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validation_inputs(images: list[Path]) -> tuple[list[str], list[np.ndarray]]:
    rng = np.random.default_rng(0)
    names = ["seeded-normal", "black", "white", "seeded-rgb"]
    tensors = [rng.standard_normal((1, 3, 224, 224), dtype=np.float32)]
    for image in (
        Image.new("RGB", (281, 195), "black"),
        Image.new("RGB", (195, 281), "white"),
        Image.fromarray(rng.integers(0, 256, (251, 309, 3), dtype=np.uint8)),
    ):
        tensors.append(image_to_chw_float(image, (224, 224)))
    for path in images:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        for label, variant in (
            ("original", image),
            ("mirror", ImageOps.mirror(image)),
            ("brightness", ImageEnhance.Brightness(image).enhance(0.85)),
            ("crop", image.crop((image.width // 6, image.height // 6, image.width, image.height))),
        ):
            names.append(f"{path.name}:{label}")
            tensors.append(image_to_chw_float(variant, (224, 224)))
    return names, tensors


def validate(model, session, names, tensors, torch) -> dict:
    from iPhoto.pets.pipeline import _cluster_embeddings_bounded_single_link

    old, new, elapsed = [], [], []
    for name, tensor in zip(names, tensors, strict=True):
        with torch.no_grad():
            expected = model(torch.from_numpy(tensor)).detach().cpu().numpy()
        started = time.perf_counter()
        actual = session.run(["embedding"], {"image": tensor})[0]
        elapsed.append((time.perf_counter() - started) * 1000)
        if actual.shape != (1, 384) or actual.dtype != np.float32 or not np.isfinite(actual).all():
            raise RuntimeError(f"Invalid ONNX output for {name}")
        np.testing.assert_allclose(actual, expected, rtol=2e-4, atol=2e-4, err_msg=name)
        old.append(expected[0])
        new.append(actual[0])
    old_raw, new_raw = np.stack(old), np.stack(new)
    old = old_raw / np.linalg.norm(old_raw, axis=1, keepdims=True)
    new = new_raw / np.linalg.norm(new_raw, axis=1, keepdims=True)
    similarities = np.sum(old.astype(np.float64) * new.astype(np.float64), axis=1)
    if float(similarities.min()) < 0.9999:
        raise RuntimeError("Embedding cosine equivalence failed")
    old_distances = 1 - old @ old.T
    new_distances = 1 - new @ new.T
    # Include all observed pair distances on either side of a small numeric
    # guard band, as well as the production link and diameter thresholds.
    thresholds = {0.42, 0.63}
    for distance in old_distances[np.triu_indices(len(old), 1)]:
        thresholds.update((max(0.0, float(distance) - 1e-5), float(distance) + 1e-5))
    for threshold in thresholds:
        # The same production bounded-single-link implementation consumes both
        # spaces. No substitute clustering algorithm participates in this check.
        kwargs = {"distance_threshold": threshold, "compatibility_keys": ["same"] * len(old)}
        a = _cluster_embeddings_bounded_single_link(old, **kwargs)
        b = _cluster_embeddings_bounded_single_link(new, **kwargs)
        if not np.array_equal(a[:, None] == a, b[:, None] == b):
            raise RuntimeError(f"Clustering equivalence failed at {threshold}")
    return {
        "samples": names,
        "sample_count": len(names),
        "raw_max_abs_error": float(np.max(np.abs(old_raw - new_raw))),
        "normalized_max_abs_error": float(np.max(np.abs(old - new))),
        "minimum_embedding_cosine": float(similarities.min()),
        "pairwise_distance_max_abs_error": float(np.max(np.abs(old_distances - new_distances))),
        "production_cluster_decisions_equal": True,
        "thresholds_checked": len(thresholds),
        "threshold_guard_band": 1e-5,
        "cpu_inference_ms": elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--torchscript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--update-manifest", action="store_true")
    parser.add_argument(
        "--reference-output",
        type=Path,
        help="Save TorchScript inputs/raw outputs for Torch-free CI runners",
    )
    args = parser.parse_args()
    if args.torchscript.stat().st_size != SOURCE_SIZE or sha256(args.torchscript) != SOURCE_SHA256:
        raise RuntimeError("Expected the verified DINOv2 model from the official v1 combined ZIP")

    import onnx
    import onnxruntime as ort
    import torch

    torch.set_num_threads(2)
    torch.manual_seed(0)
    model = torch.jit.load(str(args.torchscript), map_location="cpu").eval()
    names, tensors = validation_inputs(args.image)
    if args.reference_output:
        with torch.no_grad():
            references = np.stack(
                [model(torch.from_numpy(tensor)).cpu().numpy() for tensor in tensors]
            )
        args.reference_output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(args.reference_output, inputs=np.stack(tensors), raw_outputs=references)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dinov2-export-", dir=args.output.parent) as stage:
        candidate = Path(stage) / args.output.name
        with torch.no_grad():
            torch.onnx.export(
                model,
                torch.from_numpy(tensors[0]),
                str(candidate),
                input_names=["image"],
                output_names=["embedding"],
                opset_version=OPSET,
                dynamo=False,
                do_constant_folding=True,
                external_data=False,
            )
        graph = onnx.load(str(candidate))
        # Local CUDA/CPU wheel suffixes are tooling details, not model identity.
        # Canonicalize the exporter tag so the same verified source reproduces
        # the release bytes across the isolated macOS/Linux export environments.
        graph.producer_name = "pytorch"
        graph.producer_version = importlib.metadata.version("torch").split("+")[0]
        onnx.helper.set_model_props(
            graph,
            {
                "source_repository": "facebookresearch/dinov2",
                "source_revision": SOURCE_REVISION,
                "source_sha256": SOURCE_SHA256,
                "embedding_pipeline_version": "dinov2-vits14-imagenet-normalized-v1",
            },
        )
        onnx.checker.check_model(graph, full_check=True)
        onnx.save_model(graph, str(candidate), save_as_external_data=False)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(candidate), sess_options=options, providers=["CPUExecutionProvider"]
        )
        evidence = validate(model, session, names, tensors, torch)
        session = None
        identity = {"onnx_sha256": sha256(candidate), "onnx_size": candidate.stat().st_size}
        if not args.update_manifest:
            current = json.loads((ROOT / "src/iPhoto/pets/model_manifest.json").read_text())[
                "embedder"
            ]
            if any(current[key] != value for key, value in identity.items()):
                raise RuntimeError(
                    f"Export differs from the checked-in release identity: {identity}"
                )
        os.replace(candidate, args.output)
    manifest_path = ROOT / "src/iPhoto/pets/model_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    embedder = manifest["embedder"]
    embedder.update(identity)
    embedder.update(
        {
            "filename": "embedding/dinov2_vits14/dinov2_vits14.onnx",
            "format": "onnx",
            "opset": OPSET,
            "onnx_url": None,
            "input_name": "image",
            "output_name": "embedding",
            "dtype": "float32",
            "input_shape": [1, 3, 224, 224],
            "output_shape": [1, 384],
            "layout": "NCHW",
            "channel_order": "RGB",
            "normalization": {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
            "resize": "ImageOps.fit:BICUBIC",
            "output_normalization": "L2",
            "source_sha256": SOURCE_SHA256,
        }
    )
    for key in ("torchscript_url", "torchscript_sha256", "torchscript_size"):
        embedder.pop(key, None)
    metadata = {
        key: value for key, value in embedder.items() if key not in {"filename", "onnx_url"}
    }
    atomic_json(args.output.with_suffix(".onnx.metadata.json"), metadata)
    if args.update_manifest:
        manifest["schema_version"] = 2
        atomic_json(manifest_path, manifest)
    report = {
        "source_revision": SOURCE_REVISION,
        "source_sha256": SOURCE_SHA256,
        "opset": OPSET,
        **identity,
        **evidence,
        "providers": ["CPUExecutionProvider"],
        "onnx_checker": "passed",
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("torch", "onnx", "onnxruntime", "numpy", "Pillow")
        },
        "real_image_sha256": {path.name: sha256(path) for path in args.image},
        "historical_library_validation": "Pending Manual Validation",
    }
    atomic_json(args.report, report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
