from __future__ import annotations

from iPhoto.pets import pipeline as pet_pipeline


def test_pet_embedder_download_url_prefers_environment(monkeypatch) -> None:
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "torchscript_url",
        "https://manifest.example/dinov2_vits14.pt",
    )
    monkeypatch.setenv(
        pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV,
        "https://mirror.example/dinov2_vits14.pt",
    )

    assert pet_pipeline.pet_embedder_model_url() == (
        "https://mirror.example/dinov2_vits14.pt"
    )


def test_pet_embedder_download_url_falls_back_to_manifest(monkeypatch) -> None:
    monkeypatch.delenv(pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV, raising=False)
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "torchscript_url",
        "https://manifest.example/dinov2_vits14.pt",
    )

    assert pet_pipeline.pet_embedder_model_url() == (
        "https://manifest.example/dinov2_vits14.pt"
    )


def test_pet_embedder_download_url_can_be_explicitly_configured_when_manifest_is_empty(
    monkeypatch,
) -> None:
    monkeypatch.setitem(pet_pipeline._EMBEDDER_MANIFEST, "torchscript_url", None)
    monkeypatch.setenv(
        pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV,
        "https://models.example/dinov2_vits14.pt",
    )

    assert pet_pipeline.pet_embedder_model_url() == (
        "https://models.example/dinov2_vits14.pt"
    )
    assert len(str(pet_pipeline._EMBEDDER_MANIFEST["torchscript_sha256"])) == 64
    assert int(pet_pipeline._EMBEDDER_MANIFEST["torchscript_size"]) > 0
