from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline

pet_impl = pet_pipeline._impl


def test_resolver_does_not_delete_metadata_during_dino_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = Path("embedding") / "dinov2_vits14"
    bundled = tmp_path / "bundled"
    user_cache = tmp_path / "cache"
    final_dir = user_cache / relative
    final_dir.mkdir(parents=True)
    model_path = final_dir / "dinov2_vits14.pt"
    final_metadata = pet_pipeline._dinov2_metadata_path(model_path)

    candidate = tmp_path / "candidate.pt"
    candidate_metadata = tmp_path / "candidate.pt.metadata.json"
    candidate.write_bytes(b"model")
    candidate_metadata.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pet_impl, "pet_model_override_dir", lambda: None)
    monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_impl, "user_pet_model_cache_dir", lambda: user_cache)
    monkeypatch.setattr(
        pet_impl,
        "pet_model_search_roots",
        lambda: (bundled, user_cache),
    )
    monkeypatch.setattr(pet_impl, "pet_model_install_root", lambda: user_cache)

    real_replace = Path.replace
    resolver_results: list[Path] = []

    def interleaved_replace(self: Path, target: Path):
        result = real_replace(self, target)
        if self == candidate_metadata:
            assert final_metadata.is_file()
            assert not model_path.exists()
            resolver_results.append(
                pet_pipeline.resolve_pet_model_path(relative, directory=True)
            )
            assert final_metadata.is_file()
            assert not model_path.exists()
        return result

    monkeypatch.setattr(Path, "replace", interleaved_replace)

    pet_pipeline._publish_dinov2_cache_pair(
        candidate,
        candidate_metadata,
        model_path,
    )

    assert resolver_results == [final_dir]
    assert model_path.read_bytes() == b"model"
    assert final_metadata.read_text(encoding="utf-8") == "{}"
