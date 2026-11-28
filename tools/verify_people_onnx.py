#!/usr/bin/env python3
"""Compare the shared-runtime injection to the existing InsightFace CPU wrapper."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iPhoto.people.image_utils import load_image_rgb, pil_image_to_bgr
from iPhoto.people.pipeline import FaceClusterPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import os

    os.environ["IPHOTO_RECOGNITION_PROVIDERS"] = "CPUExecutionProvider"
    pipeline = FaceClusterPipeline(model_root=args.model_root)
    with contextlib.redirect_stdout(io.StringIO()):
        current = pipeline._ensure_face_analysis()
        from insightface.app.face_analysis import FaceAnalysis
        from insightface.model_zoo import get_model

        baseline = FaceAnalysis.__new__(FaceAnalysis)
        baseline.models = {
            key: get_model(
                str(args.model_root / "buffalo_s" / name),
                download=False,
                providers=["CPUExecutionProvider"],
            )
            for key, name in (("detection", "det_500m.onnx"), ("recognition", "w600k_mbf.onnx"))
        }
        baseline.det_model = baseline.models["detection"]
        baseline.prepare(ctx_id=-1, det_size=(640, 640))
        bgr = pil_image_to_bgr(load_image_rgb(args.image))
        before, after = baseline.get(bgr), current.get(bgr)
    try:
        if not before or len(before) != len(after):
            raise RuntimeError("Fixture must have faces and matching detection count")
        records = []
        for old, new in zip(before, after, strict=True):
            np.testing.assert_allclose(new.bbox, old.bbox, rtol=0, atol=1e-3)
            np.testing.assert_allclose(new.kps, old.kps, rtol=0, atol=1e-3)
            np.testing.assert_allclose(new.embedding, old.embedding, rtol=1e-4, atol=1e-4)
            cosine = float(np.dot(old.normed_embedding, new.normed_embedding))
            if cosine < 0.9999:
                raise RuntimeError("Face embedding compatibility failed")
            records.append(
                {
                    "bbox_max_abs_error": float(np.max(np.abs(old.bbox - new.bbox))),
                    "landmark_max_abs_error": float(np.max(np.abs(old.kps - new.kps))),
                    "raw_embedding_max_abs_error": float(
                        np.max(np.abs(old.embedding - new.embedding))
                    ),
                    "normalized_embedding_cosine": cosine,
                }
            )
        report = {
            "fixture": args.image.name,
            "face_count": len(after),
            "providers": ["CPUExecutionProvider"],
            "detection_nms_alignment_embedding_equal_within_tolerance": True,
            "faces": records,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        pipeline.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
