from pathlib import Path

import pytest

from iPhoto.pets import model_bootstrap
from iPhoto.pets import pipeline as pet_pipeline


def _unexpected_call(reason: str):
    def _call(*_args, **_kwargs):
        raise AssertionError(reason)

    return _call


def _stub_detector(monkeypatch, calls: list[tuple[str, Path]]) -> None:
    def _ensure(path, **_kwargs):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"detector")
        calls.append(("detector", path))
        return path

    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", _ensure)


def test_missing_models_trigger_detector_then_fixed_dinov2_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, Path | str]] = []
    _stub_detector(monkeypatch, calls)
    url = "https://models.example/dinov2_vits14.pt"
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: url)
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        lambda path, *, url: calls.append(("embedder-download", f"{path}|{url}")),
    )

    changed = model_bootstrap.ensure_pet_model_artifacts(tmp_path)

    expected_model_path = tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    assert changed is True
    assert calls == [
        ("detector", tmp_path / "detector" / "yolox_nano_coco.onnx"),
        ("embedder-download", f"{expected_model_path}|{url}"),
    ]


def test_missing_dinov2_url_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _stub_detector(monkeypatch, [])
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: None)
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        _unexpected_call("must not download without a fixed URL"),
    )

    with pytest.raises(pet_pipeline.PetModelUnavailableError, match="no fixed DINOv2"):
        model_bootstrap.ensure_pet_model_artifacts(tmp_path)


def test_model_auto_download_disabled_skips_acquisition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        _unexpected_call("must not download"),
    )

    assert (
        model_bootstrap.ensure_pet_model_artifacts(
            tmp_path,
            allow_model_download=False,
        )
        is False
    )


def test_existing_verified_models_do_not_trigger_embedder_acquisition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(tmp_path))
    detector_path = tmp_path / "detector" / "yolox_nano_coco.onnx"
    detector_path.parent.mkdir(parents=True)
    detector_path.write_bytes(b"detector")
    embedder_path = tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    embedder_path.parent.mkdir(parents=True)
    embedder_path.write_bytes(b"embedder")

    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda path, **_kwargs: Path(path),
    )
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_dinov2_cache_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_downloaded_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        _unexpected_call("verified model must be reused"),
    )

    assert model_bootstrap.ensure_pet_model_artifacts(tmp_path) is False


def test_existing_cache_models_are_reused_before_extension_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    detector_path = cache / "detector" / "yolox_nano_coco.onnx"
    embedder_path = cache / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    detector_path.parent.mkdir(parents=True)
    embedder_path.parent.mkdir(parents=True)
    detector_path.write_bytes(b"detector")
    embedder_path.write_bytes(b"embedder")

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_downloaded_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_dinov2_cache_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        _unexpected_call("valid fallback models must be reused"),
    )
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        _unexpected_call("valid fallback detector must be reused"),
    )

    assert model_bootstrap.ensure_pet_model_artifacts() is False


def test_explicit_override_is_authoritative_without_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    override = tmp_path / "override"
    extension = tmp_path / "extension"
    cache = tmp_path / "cache"
    monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    def _ensure(path, **_kwargs):
        raise AssertionError("must not install outside an authoritative override")

    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", _ensure)

    with pytest.raises(
        pet_pipeline.PetModelUnavailableError,
        match="authoritative and does not fall back",
    ):
        model_bootstrap.ensure_pet_model_artifacts(cache)


def test_invalid_embedder_is_replaced_by_fixed_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    detector_path = cache / "detector" / "yolox_nano_coco.onnx"
    detector_path.parent.mkdir(parents=True)
    detector_path.write_bytes(b"detector")

    invalid_embedder_dir = cache / "embedding" / "dinov2_vits14"
    invalid_embedder = invalid_embedder_dir / "dinov2_vits14.pt"
    invalid_embedder_dir.mkdir(parents=True)
    invalid_embedder.write_bytes(b"invalid")

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda path, **_kwargs: Path(path),
    )

    def _validate(path, **_kwargs):
        if Path(path) == invalid_embedder:
            raise RuntimeError("invalid")

    downloaded: list[Path] = []
    monkeypatch.setattr(pet_pipeline, "_validate_dinov2_cache_metadata", _validate)
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_downloaded_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: "https://models.example")
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        lambda path, **_kwargs: downloaded.append(Path(path)),
    )

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert not invalid_embedder.exists()
    expected_embedder = extension / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    assert downloaded == [expected_embedder]
