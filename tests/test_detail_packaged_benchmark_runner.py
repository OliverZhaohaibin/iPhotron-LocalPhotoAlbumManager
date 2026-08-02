from __future__ import annotations

import json

from tools.run_detail_packaged_benchmark import _copy_benchmark_library, _load_manifest


def test_manifest_allows_samples_without_switch_paths(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "samples": [
                    {"path": "a.jpg", "category": "single"},
                    {
                        "path": "b.jpg",
                        "category": "rapid",
                        "switch_paths": ["a.jpg", "b.jpg"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = _load_manifest(manifest)

    assert len(payload["samples"]) == 2  # noqa: S101


def test_copy_flattens_samples_and_preserves_live_photo_stem(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "IMG_0001.MOV").write_bytes(b"motion")
    (source / "nested" / "IMG_0001.HEIC").write_bytes(b"still")
    (source / "other").mkdir()
    (source / "other" / "IMG_0001.MOV").write_bytes(b"other-motion")

    samples = _copy_benchmark_library(
        source,
        destination,
        [
            {"path": "nested/IMG_0001.MOV", "category": "live"},
            {
                "path": "other/IMG_0001.MOV",
                "category": "switch",
                "switch_paths": ["nested/IMG_0001.MOV"],
            },
        ],
    )

    first_motion = destination / samples[0]["path"]
    first_still = first_motion.with_suffix(".HEIC")
    second_motion = destination / samples[1]["path"]
    assert first_motion.parent == destination  # noqa: S101
    assert first_motion.read_bytes() == b"motion"  # noqa: S101
    assert first_still.read_bytes() == b"still"  # noqa: S101
    assert second_motion.read_bytes() == b"other-motion"  # noqa: S101
    assert samples[1]["switch_paths"] == [samples[0]["path"]]  # noqa: S101
