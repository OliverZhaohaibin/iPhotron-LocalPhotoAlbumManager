from __future__ import annotations

import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.pets import pipeline as pet_pipeline


def _hold_dino_lock(model_path: str, ready, release) -> None:
    from iPhoto.pets import pipeline

    with pipeline._dinov2_acquisition_lock(Path(model_path)):
        ready.set()
        release.wait(15)


def _wait_for_dino_lock(model_path: str, acquired) -> None:
    from iPhoto.pets import pipeline

    with pipeline._dinov2_acquisition_lock(Path(model_path)):
        acquired.set()


def test_dino_acquisition_lock_serializes_processes(tmp_path: Path) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14.pt"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    acquired = context.Event()
    holder = context.Process(
        target=_hold_dino_lock,
        args=(str(model_path), ready, release),
    )
    waiter = context.Process(
        target=_wait_for_dino_lock,
        args=(str(model_path), acquired),
    )

    try:
        holder.start()
        assert ready.wait(10), "first process did not acquire the DINOv2 cache lock"
        waiter.start()
        assert not acquired.wait(0.5), "second process bypassed the DINOv2 cache lock"
        release.set()
        assert acquired.wait(10), "second process did not acquire the released DINOv2 cache lock"
        holder.join(10)
        waiter.join(10)
        assert holder.exitcode == 0
        assert waiter.exitcode == 0
    finally:
        release.set()
        for process in (holder, waiter):
            if process.is_alive():
                process.terminate()
            process.join(5)


def test_waiting_builder_reuses_cache_published_by_first_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14.pt"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"already-published")
    pet_pipeline._dinov2_metadata_path(model_path).write_text("{}", encoding="utf-8")

    loaded = object()
    load_calls: list[tuple[Path, str]] = []

    def fake_load(path: str, *, map_location: str):
        load_calls.append((Path(path), map_location))
        return loaded

    def fail_download(*_args, **_kwargs):
        raise AssertionError("a waiting builder must not download after another builder publishes")

    monkeypatch.setattr(pet_pipeline, "_validate_dinov2_cache_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(pet_pipeline, "_download_file", fail_download)
    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)

    embedder = pet_pipeline._DinoV2Embedder.__new__(pet_pipeline._DinoV2Embedder)
    embedder._model_name = "dinov2_vits14"
    embedder._torch = SimpleNamespace(jit=SimpleNamespace(load=fake_load))

    result = embedder._build_verified_dinov2_cpu_cache(model_path)

    assert result is loaded
    assert load_calls == [(model_path, "cpu")]


def test_dino_publish_makes_model_visible_after_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.pt"
    candidate_metadata = tmp_path / "candidate.pt.metadata.json"
    model_path = tmp_path / "published" / "dinov2_vits14.pt"
    final_metadata = pet_pipeline._dinov2_metadata_path(model_path)
    model_path.parent.mkdir(parents=True)
    candidate.write_bytes(b"model")
    candidate_metadata.write_text("{}", encoding="utf-8")

    real_replace = Path.replace
    publish_order: list[Path] = []

    def record_replace(self: Path, target: Path):
        if self in {candidate, candidate_metadata}:
            publish_order.append(self)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", record_replace)

    pet_pipeline._publish_dinov2_cache_pair(
        candidate,
        candidate_metadata,
        model_path,
    )

    assert publish_order == [candidate_metadata, candidate]
    assert model_path.read_bytes() == b"model"
    assert final_metadata.read_text(encoding="utf-8") == "{}"
