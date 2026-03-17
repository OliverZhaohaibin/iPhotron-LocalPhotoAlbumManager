from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import xxhash

from iPhoto import _native
from iPhoto.core.pairing import _pair_live_python, pair_live
from iPhoto.infrastructure.services.metadata_provider import ExifToolMetadataProvider
from iPhoto.infrastructure.services.metadata_provider import _parse_iso8601_metadata
from iPhoto.io.discovery import _discover_with_callback_fallback
from iPhoto.utils.pathutils import expand_globs, should_include_rel_expanded


def _native_required() -> bool:
    return _native.get_runtime_status().runtime_mode == _native.RUNTIME_MODE_C_EXTENSION


def _python_parse_iso8601(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000)


def _python_compute_file_id(path: Path) -> str:
    threshold = 2 * 1024 * 1024
    with path.open("rb") as handle:
        file_size = path.stat().st_size
        if file_size <= threshold:
            hasher = xxhash.xxh3_128()
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
            return hasher.hexdigest()

        hasher = xxhash.xxh3_128()
        hasher.update(file_size.to_bytes(8, "little"))

        chunk_size = 256 * 1024
        hasher.update(handle.read(chunk_size))
        if file_size > chunk_size * 2:
            handle.seek(file_size // 2 - chunk_size // 2)
            hasher.update(handle.read(chunk_size))
        if file_size > chunk_size:
            handle.seek(max(0, file_size - chunk_size))
            hasher.update(handle.read(chunk_size))
        return hasher.hexdigest()


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_iso8601_matches_python() -> None:
    value = "2024-06-10T09:30:00.123456Z"
    assert _native.parse_iso8601_to_unix_us(value) == _python_parse_iso8601(value)


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_metadata_parse_matches_python() -> None:
    value = "2024-01-01T12:00:00Z"
    ts, year, month = _parse_iso8601_metadata(value) or (None, None, None)
    assert ts == _python_parse_iso8601(value)
    assert year == 2024
    assert month == 1


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_content_id_normalisation_matches_python() -> None:
    assert _native.normalise_content_id("  ABcD-1234  ") == "abcd-1234"


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_hash_matches_python_strategy(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"x" * (3 * 1024 * 1024))

    native_hash = _native.compute_file_id(sample)
    python_hash = _python_compute_file_id(sample)

    assert native_hash is not None
    assert native_hash == python_hash


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_discovery_matches_fallback(tmp_path: Path) -> None:
    root = tmp_path
    (root / "photo.jpg").write_bytes(b"x")
    (root / ".iPhoto").mkdir()
    (root / ".iPhoto" / "skip.jpg").write_bytes(b"x")
    (root / "sub").mkdir()
    (root / "sub" / "clip.mov").write_bytes(b"x")

    include = expand_globs(["**/*.{jpg,mov}"])
    exclude = expand_globs(["**/.iPhoto/**"])
    native_paths: list[str] = []
    fallback_paths: list[str] = []

    count = _native.discover_files(
        root,
        include_globs=include,
        exclude_globs=exclude,
        skip_dir_names=(".iPhoto",),
        on_found=lambda path, _rel: native_paths.append(path.relative_to(root).as_posix()) or False,
    )
    fallback_count = _discover_with_callback_fallback(
        root,
        include_globs=include,
        exclude_globs=exclude,
        supported_extensions=(),
        skip_dir_names=(".iPhoto",),
        skip_hidden_dirs=False,
        stop_event=None,
        on_found=lambda path, _rel: fallback_paths.append(path.relative_to(root).as_posix()) or False,
    )

    assert count == fallback_count
    assert sorted(native_paths) == sorted(fallback_paths)


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_discovery_chunks_match_fallback(tmp_path: Path) -> None:
    root = tmp_path
    (root / "photo.jpg").write_bytes(b"x")
    (root / "sub").mkdir()
    (root / "sub" / "clip.mov").write_bytes(b"x")

    include = expand_globs(["**/*.{jpg,mov}"])
    exclude = expand_globs(["**/.iPhoto/**"])
    native_paths = [
        item.rel_path
        for chunk in (
            _native.iter_discovery_chunks(
                root,
                include_globs=include,
                exclude_globs=exclude,
                skip_dir_names=(".iPhoto",),
            )
            or ()
        )
        for item in chunk
    ]
    fallback_paths: list[str] = []
    _discover_with_callback_fallback(
        root,
        include_globs=include,
        exclude_globs=exclude,
        supported_extensions=(),
        skip_dir_names=(".iPhoto",),
        skip_hidden_dirs=False,
        stop_event=None,
        on_found=lambda path, _rel: fallback_paths.append(path.relative_to(root).as_posix()) or False,
    )

    assert sorted(native_paths) == sorted(fallback_paths)


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_prepare_scan_chunk_matches_normalize_metadata(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"video")
    provider = ExifToolMetadataProvider()

    prepared = provider.prepare_scan_chunk(tmp_path, [clip], {})
    assert prepared is not None
    assert len(prepared) == 1

    _, row = prepared[0]
    fallback_row = provider.normalize_metadata(tmp_path, clip, {})

    assert row["id"] == fallback_row["id"]
    assert row["bytes"] == fallback_row["bytes"]
    assert row["ts"] == fallback_row["ts"]
    assert row["year"] == fallback_row["year"]
    assert row["month"] == fallback_row["month"]
    assert row["media_type"] == fallback_row["media_type"]


@pytest.mark.skipif(not _native_required(), reason="native scan extension not available")
def test_native_pair_rows_match_python_pairing() -> None:
    rows = [
        {
            "rel": "IMG_0001.HEIC",
            "mime": "image/heic",
            "dt": "2024-01-01T12:00:00Z",
            "content_id": "CID1",
        },
        {
            "rel": "IMG_0001.MOV",
            "mime": "video/quicktime",
            "dt": "2024-01-01T12:00:00Z",
            "content_id": "CID1",
            "dur": 1.5,
            "still_image_time": 0.1,
        },
    ]

    native_groups = pair_live(rows)
    fallback_groups = _pair_live_python(rows)

    assert [(group.still, group.motion, group.confidence) for group in native_groups] == [
        (group.still, group.motion, group.confidence) for group in fallback_groups
    ]


def test_should_include_rel_expanded_preserves_matching() -> None:
    include = expand_globs(["*.{jpg,png}"])
    exclude = expand_globs(["bad.jpg"])

    assert should_include_rel_expanded("good.jpg", include, exclude)
    assert should_include_rel_expanded("good.png", include, exclude)
    assert not should_include_rel_expanded("bad.jpg", include, exclude)
    assert not should_include_rel_expanded("good.txt", include, exclude)
