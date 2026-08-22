from pathlib import Path
import errno

import hashlib
import json

import pytest

from iPhoto.pets import pipeline as pet_pipeline
from iPhoto.pets.errors import PetModelUnavailableError
from iPhoto.pets import model_bootstrap


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


def test_existing_bundled_models_do_not_require_any_writable_install_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled = tmp_path / "bundled" / "pets"
    cache = tmp_path / "cache" / "pets"
    detector_relative = Path("detector/yolox_nano_coco.onnx")
    embedder_relative = Path("embedding/dinov2_vits14/dinov2_vits14.pt")
    detector_path = bundled / detector_relative
    embedder_path = bundled / embedder_relative
    detector_path.parent.mkdir(parents=True)
    embedder_path.parent.mkdir(parents=True)
    detector_path.write_bytes(b"detector")
    embedder_path.write_bytes(b"embedder")

    manifest = pet_pipeline.PET_MODEL_MANIFEST["embedder"]
    digest = hashlib.sha256(embedder_path.read_bytes()).hexdigest()
    monkeypatch.setitem(manifest, "torchscript_sha256", digest)
    monkeypatch.setitem(manifest, "torchscript_size", embedder_path.stat().st_size)
    embedder_path.with_name("dinov2_vits14.pt.metadata.json").write_text(
        json.dumps(
            {
                "model_name": "dinov2_vits14",
                "source_repository": manifest["source_repository"],
                "source_revision": manifest["source_revision"],
                "torchscript_sha256": digest,
                "torchscript_size": embedder_path.stat().st_size,
                "input_shape": manifest["input_shape"],
                "output_shape": manifest["output_shape"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "_validate_downloaded_file", lambda *_args, **_: None)
    monkeypatch.setattr(
        pet_pipeline,
        "_directory_is_writable",
        lambda _path: pytest.fail("existing artifacts must not probe writability"),
    )
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        _unexpected_call("verified bundled detector must be reused"),
    )
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        _unexpected_call("verified bundled embedder must be reused"),
    )

    assert model_bootstrap.ensure_pet_model_artifacts() is False


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


def test_explicit_model_root_is_authoritative_for_lookup_and_acquisition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    explicit = tmp_path / "explicit"
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(
        pet_pipeline,
        "pet_model_search_roots",
        _unexpected_call("explicit root must bypass global resolver"),
    )

    calls: list[tuple[str, Path]] = []
    _stub_detector(monkeypatch, calls)
    url = "https://models.example/dinov2_vits14.pt"
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: url)
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        lambda path, *, url: calls.append(("embedder", path)),
    )

    changed = model_bootstrap.ensure_pet_model_artifacts(explicit)

    assert changed is True
    assert calls == [
        ("detector", explicit / "detector" / "yolox_nano_coco.onnx"),
        ("embedder", explicit / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"),
    ]


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


def test_direct_pipeline_rejects_conflicting_authoritative_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(tmp_path / "authoritative"))

    with pytest.raises(
        pet_pipeline.PetModelUnavailableError,
        match="IPHOTO_PET_MODEL_DIR is authoritative",
    ):
        pet_pipeline.PetClusterPipeline(model_root=tmp_path)


def test_direct_pipeline_accepts_matching_authoritative_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(tmp_path))

    pipeline = pet_pipeline.PetClusterPipeline(
        model_root=tmp_path,
        allow_model_download=False,
    )

    assert pipeline._model_root == tmp_path


def test_wrapped_detector_toctou_permission_failure_falls_back_to_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    detector_relative = Path("detector/yolox_nano_coco.onnx")
    embedder_relative = Path("embedding/dinov2_vits14/dinov2_vits14.pt")
    requested_detector = extension / detector_relative
    fallback_detector = cache / detector_relative
    fallback_embedder = cache / embedder_relative

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", lambda: extension)

    def _ensure(path, **_kwargs):
        if Path(path) == requested_detector:
            raise RuntimeError(
                "Pet scanning unavailable: filesystem permission denied for "
                "the YOLOX detector model."
            ) from OSError(errno.EACCES, "Permission denied")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"detector")

    def _download(path, *, url):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"embedder")

    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", _ensure)
    monkeypatch.setattr(model_bootstrap, "_download_dinov2_release", _download)
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: "https://models.example")

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert fallback_detector.read_bytes() == b"detector"
    assert fallback_embedder.read_bytes() == b"embedder"


@pytest.mark.parametrize("errno_value", [errno.EACCES, errno.EROFS])
def test_dinov2_download_wrapped_filesystem_error_falls_back_to_cache(
    tmp_path: Path,
    monkeypatch,
    errno_value,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    detector_relative = Path("detector/yolox_nano_coco.onnx")
    embedder_relative = Path("embedding/dinov2_vits14/dinov2_vits14.pt")
    requested_embedder = extension / embedder_relative
    fallback_embedder = cache / embedder_relative
    requested_embedder.parent.mkdir(parents=True)

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", lambda: extension)
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda path, **_kwargs: _write_detector(path),
    )

    def _download(_url, destination, **_kwargs):
        if Path(destination) == requested_embedder:
            raise RuntimeError(
                f"filesystem permission denied for {destination.name}."
            ) from OSError(errno_value, "Filesystem denied")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"embedder")

    monkeypatch.setattr(pet_pipeline, "_download_file", _download)
    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_dinov2_cache_metadata",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pet_pipeline,
        "pet_embedder_model_url",
        lambda: "https://models.example",
    )

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert not requested_embedder.exists()
    assert fallback_embedder.read_bytes() == b"embedder"


def _write_detector(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"detector")


def test_dinov2_permission_failure_preserves_wrapped_cause(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original = OSError(errno.EACCES, "Permission denied")
    model_path = tmp_path / "dinov2_vits14.pt"

    def _download(*_args, **_kwargs):
        raise RuntimeError("filesystem permission denied.") from original

    monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)
    monkeypatch.setattr(pet_pipeline, "_download_file", _download)
    monkeypatch.setattr(
        pet_pipeline,
        "_validate_dinov2_cache_metadata",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(PermissionError) as exc_info:
        model_bootstrap._download_dinov2_release(
            model_path,
            url="https://models.example",
        )

    assert exc_info.value.__cause__ is not None
    assert exc_info.value.__cause__.__cause__ is original


def test_validation_failure_does_not_fall_back_to_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    requested_detector = extension / "detector/yolox_nano_coco.onnx"

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", lambda: extension)

    def _ensure(_path, **_kwargs):
        raise RuntimeError("SHA-256 verification failed")

    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", _ensure)

    with pytest.raises(PetModelUnavailableError, match="SHA-256 verification failed"):
        model_bootstrap.ensure_pet_model_artifacts()

    assert not (cache / "detector").exists()


def test_invalid_bundled_artifacts_install_to_user_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extension = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    invalid_detector = extension / "detector" / "yolox_nano_coco.onnx"
    invalid_embedder_dir = extension / "embedding" / "dinov2_vits14"
    invalid_embedder = invalid_embedder_dir / "dinov2_vits14.pt"
    invalid_detector.parent.mkdir(parents=True)
    invalid_embedder_dir.mkdir(parents=True)
    invalid_detector.write_bytes(b"invalid-detector")
    invalid_embedder.write_bytes(b"invalid-embedder")

    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: extension)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)
    monkeypatch.setattr(pet_pipeline, "pet_model_install_root", lambda: extension)

    detector_calls: list[Path] = []

    def _ensure_detector(path, **_kwargs):
        detector_calls.append(Path(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"detector")
        return path

    def _validate_detector(path, **_kwargs):
        if Path(path) == invalid_detector:
            raise RuntimeError("invalid detector")

    def _validate_embedder(path, **_kwargs):
        if Path(path) == invalid_embedder:
            raise RuntimeError("invalid embedder")

    downloaded: list[Path] = []
    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", _ensure_detector)
    monkeypatch.setattr(pet_pipeline, "_validate_downloaded_file", _validate_detector)
    monkeypatch.setattr(pet_pipeline, "_validate_dinov2_cache_metadata", _validate_embedder)
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: "https://models.example")
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        lambda path, **_kwargs: downloaded.append(Path(path)),
    )

    assert model_bootstrap.ensure_pet_model_artifacts() is True
    assert invalid_detector.read_bytes() == b"invalid-detector"
    assert invalid_embedder.read_bytes() == b"invalid-embedder"
    assert detector_calls == [cache / "detector" / "yolox_nano_coco.onnx"]
    expected_embedder = cache / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt"
    assert downloaded == [expected_embedder]


def test_pipeline_missing_model_with_downloads_disabled_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: tmp_path / "extension")
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: tmp_path / "cache")
    pipeline = pet_pipeline.PetClusterPipeline(
        model_root=tmp_path / "extension",
        allow_model_download=False,
    )

    with pytest.raises(
        pet_pipeline.PetModelUnavailableError,
        match="missing model artifact",
    ):
        pipeline._resolve_model_path(Path("detector/yolox_nano_coco.onnx"))
