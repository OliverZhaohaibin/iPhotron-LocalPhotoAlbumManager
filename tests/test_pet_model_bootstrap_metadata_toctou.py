import errno
from pathlib import Path

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
    requested_metadata = pet_pipeline._dinov2_metadata_path(requested_embedder)
    fallback_metadata = pet_pipeline._dinov2_metadata_path(fallback_embedder)
    attempts: list[Path] = []

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", lambda: extension)
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda path, **_kwargs: _write_detector(path),
    )
    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_dinov2_cache_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pet_pipeline,
        "pet_embedder_model_url",
        lambda: "https://models.example/dinov2_vits14.pt",
    )

    def _download(_url, destination, **_kwargs):
        destination = Path(destination)
        attempts.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"embedder")

    monkeypatch.setattr(pet_pipeline, "_download_file", _download)

    original_replace = Path.replace

    def _replace(self: Path, target: Path):
        target = Path(target)
        if target == requested_metadata:
            raise OSError(errno.EROFS, "Read-only file system")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _replace)

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert attempts == [requested_embedder, fallback_embedder]
    assert requested_embedder.read_bytes() == b"embedder"
    assert not requested_metadata.exists()
    assert fallback_embedder.read_bytes() == b"embedder"
    assert fallback_metadata.is_file()
    assert not list(requested_embedder.parent.glob(".*.tmp"))
    assert not list(requested_embedder.parent.glob(".*.probe"))


def test_failed_attempt_cleanup_preserves_shared_final_metadata(tmp_path: Path) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text("published-by-another-attempt", encoding="utf-8")

    model_bootstrap._remove_dinov2_artifact(model_path)

    assert metadata_path.read_text(encoding="utf-8") == "published-by-another-attempt"
