from __future__ import annotations

from pathlib import Path

from iPhoto.pets import pipeline as pet_pipeline
from iPhoto.pets import service as pet_service


OFFICIAL_DINOV2_VITS14_URL = (
    "https://dl.fbaipublicfiles.com/dinov2/"
    "dinov2_vits14/dinov2_vits14_pretrain.pth"
)
OFFICIAL_DINOV2_VITS14_SHA256 = (
    "b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9"
)
OFFICIAL_DINOV2_VITS14_SIZE = 88283115


def test_pet_embedder_download_url_prefers_environment(monkeypatch) -> None:
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "weights_url",
        OFFICIAL_DINOV2_VITS14_URL,
    )
    monkeypatch.setenv(
        pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV,
        "https://mirror.example/dinov2_vits14_pretrain.pth",
    )

    assert pet_pipeline.pet_embedder_model_url() == (
        "https://mirror.example/dinov2_vits14_pretrain.pth"
    )


def test_pet_embedder_download_url_falls_back_to_official_manifest(monkeypatch) -> None:
    monkeypatch.delenv(pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV, raising=False)
    monkeypatch.setitem(
        pet_pipeline._EMBEDDER_MANIFEST,
        "weights_url",
        OFFICIAL_DINOV2_VITS14_URL,
    )

    assert pet_pipeline.pet_embedder_model_url() == OFFICIAL_DINOV2_VITS14_URL


def test_pet_embedder_manifest_pins_official_download_artifact() -> None:
    manifest = pet_pipeline._EMBEDDER_MANIFEST

    assert manifest["weights_url"] == OFFICIAL_DINOV2_VITS14_URL
    assert manifest["weights_sha256"] == OFFICIAL_DINOV2_VITS14_SHA256
    assert int(manifest["weights_size"]) == OFFICIAL_DINOV2_VITS14_SIZE
    assert str(manifest.get("torchscript_url") or "") == ""


def test_pet_embedder_download_url_can_use_verified_mirror(monkeypatch) -> None:
    mirror = "https://models.example/dinov2_vits14_pretrain.pth"
    monkeypatch.setenv(pet_pipeline.PET_EMBEDDER_MODEL_URL_ENV, mirror)

    assert pet_pipeline.pet_embedder_model_url() == mirror
    assert len(str(pet_pipeline._EMBEDDER_MANIFEST["weights_sha256"])) == 64
    assert int(pet_pipeline._EMBEDDER_MANIFEST["weights_size"]) > 0


def test_shared_pet_model_dir_defaults_to_cache_lookup_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache_root = tmp_path / "cache" / "models" / "pets"
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache_root)

    assert pet_service.shared_pet_model_dir() == cache_root
    assert pet_service.pet_library_paths(tmp_path / "library").model_dir == cache_root


def test_shared_pet_model_dir_allows_explicit_override(tmp_path: Path, monkeypatch) -> None:
    override_root = tmp_path / "custom-pet-models"
    monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override_root))

    assert pet_service.shared_pet_model_dir() == override_root


def test_shared_pet_model_dir_is_lazy_default_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension_root = tmp_path / "extension" / "pets"
    cache_root = tmp_path / "cache" / "pets"
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension_root)

    calls: list[Path] = []

    def _fail_install_root(path):
        calls.append(path)
        raise AssertionError("shared lookup must not probe writable storage")

    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache_root)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", _fail_install_root)

    assert pet_service.shared_pet_model_dir() == cache_root
    assert pet_service.pet_library_paths(tmp_path / "library").model_dir == cache_root
    assert calls == []


def test_pet_model_install_root_uses_real_write_probe(tmp_path: Path, monkeypatch) -> None:
    extension_root = tmp_path / "extension" / "pets"
    cache_root = tmp_path / "cache" / "pets"
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension_root)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache_root)

    assert pet_pipeline.pet_model_install_root() == extension_root
    assert (
        any(
            path.name.startswith(".iphoto-pet-model-write-probe-")
            for path in extension_root.iterdir()
        )
        is False
    )


def test_pet_model_search_roots_are_extension_first(tmp_path: Path, monkeypatch) -> None:
    extension_root = tmp_path / "extension" / "pets"
    cache_root = tmp_path / "cache" / "pets"
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension_root)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache_root)

    assert pet_pipeline.pet_model_search_roots() == (extension_root, cache_root)
