from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.pets import pipeline as pet_pipeline


def test_dinov2_release_and_provenance_are_immutable() -> None:
    manifest = pet_pipeline._EMBEDDER_MANIFEST
    assert manifest["source_repository"] == "facebookresearch/dinov2"
    assert manifest["source_revision"] == "7764ea0f912e53c92e82eb78a2a1631e92725fc8"
    assert manifest["source_tree_sha1"] == "2a27257b79b0633b027a21014bc9360e3c1b3f43"
    assert manifest["torchscript_url"] == (
        "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
        "releases/download/pet-models-v1/dinov2_vits14.pt"
    )
    assert manifest["cache_schema_version"] == 2
    assert manifest["producer_torch_version"] == "2.12.1"


def test_production_pets_source_has_no_torch_hub_or_xformers_branch() -> None:
    source_root = Path(pet_pipeline.__file__).parent
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in source_root.glob("*.py")
    )
    assert "torch.hub" not in production_source
    assert "source=\"github\"" not in production_source
    assert "XFORMERS" not in production_source


def test_embedder_construction_stays_lazy_until_first_crop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    class ExplodingEmbedder:
        def __init__(self, model_dir: Path, **_kwargs) -> None:
            calls.append(Path(model_dir))
            raise AssertionError("real DINOv2 initialization should still be deferred")

    monkeypatch.setattr(pet_pipeline, "_DinoV2Embedder", ExplodingEmbedder)
    pipeline = pet_pipeline.PetClusterPipeline(model_root=tmp_path / "models")

    lazy = pipeline._ensure_embedder()
    assert calls == []

    with pytest.raises(AssertionError, match="still be deferred"):
        lazy.embed(None)
    assert calls == [tmp_path / "models" / "embedding" / "dinov2_vits14"]


def test_device_failure_does_not_delete_published_dino_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "embedding" / "dinov2_vits14.pt"
    metadata_path = pet_pipeline._dinov2_metadata_path(model_path)

    class FakeModel:
        def eval(self):
            return self

        def to(self, device):
            if device == "test-device":
                raise RuntimeError("device unavailable")
            return self

    def fake_verified_cpu_build(_self, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"verified-cache")
        pet_pipeline._dinov2_metadata_path(target).write_text("{}", encoding="utf-8")
        return FakeModel()

    monkeypatch.setattr(
        pet_pipeline._DinoV2Embedder,
        "_build_verified_dinov2_cpu_cache",
        fake_verified_cpu_build,
    )
    embedder = pet_pipeline._DinoV2Embedder.__new__(pet_pipeline._DinoV2Embedder)
    embedder._device = "test-device"

    with pytest.raises(pet_pipeline.PetModelUnavailableError, match="built and verified"):
        embedder._build_dinov2_cache(model_path)

    assert model_path.read_bytes() == b"verified-cache"
    assert metadata_path.is_file()


def test_dino_download_error_does_not_suggest_detector_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_download(*_args, **_kwargs):
        raise RuntimeError(
            "Pet scanning unavailable: failed to download DINOv2 checkpoint from "
            "https://example.test/model (network denied). Check your network connection, set "
            f"{pet_pipeline.PET_DETECTOR_MODEL_URL_ENV}, or install the model manually."
        )

    monkeypatch.setattr(pet_pipeline, "_original_download_file", fail_download)

    with pytest.raises(RuntimeError) as raised:
        pet_pipeline._download_file(
            "https://example.test/model",
            tmp_path / "model.bin",
            label="DINOv2 checkpoint",
            expected_sha256="0" * 64,
            max_bytes=100,
        )

    message = str(raised.value)
    assert pet_pipeline.PET_DETECTOR_MODEL_URL_ENV not in message
    assert "Check your network connection or install the model manually." in message
