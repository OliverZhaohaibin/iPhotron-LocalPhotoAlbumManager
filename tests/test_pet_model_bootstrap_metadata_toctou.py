import errno
import json
from pathlib import Path

import pytest

from iPhoto.pets import model_bootstrap
from iPhoto.pets import pipeline as pet_pipeline


def _write_detector(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"detector")


def test_dinov2_metadata_commit_erofs_falls_back_to_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    embedder_relative = Path("embedding/dinov2_vits14/dinov2_vits14.pt")
    requested_embedder = extension / embedder_relative
    fallback_embedder = cache / embedder_relative
    attempts: list[Path] = []

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda path, **_kwargs: _write_detector(path),
    )
    monkeypatch.setattr(
        pet_pipeline,
        "pet_embedder_model_url",
        lambda: "https://models.example/dinov2_vits14_pretrain.pth",
    )

    def _download(path: Path, *, url: str) -> None:
        path = Path(path)
        attempts.append(path)
        if path == requested_embedder:
            raise pet_pipeline._ModelStoragePermissionError(
                errno.EROFS,
                "Read-only file system",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"derived-cache")

    monkeypatch.setattr(model_bootstrap, "_download_dinov2_release", _download)

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert attempts == [requested_embedder, fallback_embedder]
    assert not requested_embedder.exists()
    assert fallback_embedder.read_bytes() == b"derived-cache"


def test_failed_attempt_cleanup_preserves_shared_final_artifacts(tmp_path: Path) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"published-by-another-attempt")
    metadata_path.write_text("published-metadata", encoding="utf-8")

    model_bootstrap._remove_dinov2_artifact(model_path)

    assert model_path.read_bytes() == b"published-by-another-attempt"
    assert metadata_path.read_text(encoding="utf-8") == "published-metadata"


def test_dinov2_metadata_records_source_hash_not_derived_hash(tmp_path: Path) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"derived-cache")

    model_bootstrap._write_dinov2_metadata(
        model_path,
        weights_url="https://mirror.example/dinov2_vits14_pretrain.pth",
    )

    metadata = json.loads(
        pet_pipeline._dinov2_metadata_path(model_path).read_text(encoding="utf-8")
    )
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]

    assert metadata["weights_sha256"] == manifest["weights_sha256"]
    assert metadata["weights_size"] == manifest["weights_size"]
    assert metadata["derived_torchscript_size"] == model_path.stat().st_size
    assert "torchscript_sha256" not in metadata


def test_official_checkpoint_is_verified_before_conversion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    calls: list[tuple[str, Path, str, int]] = []

    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)

    def _download(
        url: str,
        destination: Path,
        *,
        label: str,
        expected_sha256: str,
        max_bytes: int,
    ) -> None:
        calls.append(
            (
                url,
                Path(destination),
                expected_sha256,
                max_bytes,
            )
        )
        raise RuntimeError("stop after checkpoint verification contract")

    monkeypatch.setattr(pet_pipeline, "_download_file", _download)

    with pytest.raises(
        pet_pipeline.PetModelUnavailableError,
        match="stop after checkpoint verification contract",
    ):
        model_bootstrap._download_dinov2_release(
            tmp_path / "dinov2_vits14.pt",
            url=str(manifest["weights_url"]),
        )

    assert len(calls) == 1
    url, destination, expected_sha256, max_bytes = calls[0]
    assert url == manifest["weights_url"]
    assert destination.name == "dinov2_vits14_pretrain.pth"
    assert expected_sha256 == manifest["weights_sha256"]
    assert max_bytes == manifest["weights_size"]
