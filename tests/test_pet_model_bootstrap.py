from __future__ import annotations

from pathlib import Path

from iPhoto.pets import model_bootstrap
from iPhoto.pets import pipeline as pet_pipeline


def _stub_detector(monkeypatch, calls: list[tuple[str, Path | str]]) -> None:
    def ensure_detector(path: Path, *, allow_model_download: bool = True, model_url=None):
        del model_url
        assert allow_model_download is True
        calls.append(("detector", Path(path)))
        return Path(path)

    monkeypatch.setattr(pet_pipeline, "ensure_pet_detector_model", ensure_detector)


def test_missing_models_trigger_detector_then_pinned_dinov2_bootstrap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, Path | str]] = []
    _stub_detector(monkeypatch, calls)
    monkeypatch.setattr(pet_pipeline, "pet_embedder_model_url", lambda: "")
    monkeypatch.setattr(
        model_bootstrap,
        "_bootstrap_dinov2_from_pinned_source",
        lambda path: calls.append(("embedder-bootstrap", Path(path))),
    )

    changed = model_bootstrap.ensure_pet_model_artifacts(tmp_path)

    assert changed is True
    assert calls == [
        ("detector", tmp_path / "detector" / "yolox_nano_coco.onnx"),
        (
            "embedder-bootstrap",
            tmp_path / "embedding" / "dinov2_vits14" / "dinov2_vits14.pt",
        ),
    ]


def test_configured_embedder_url_is_downloaded_before_scanning(
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

    assert changed is True
    assert calls[0] == ("detector", tmp_path / "detector" / "yolox_nano_coco.onnx")
    assert calls[1] == (
        "embedder-download",
        f"{tmp_path / 'embedding' / 'dinov2_vits14' / 'dinov2_vits14.pt'}|{url}",
    )


def test_model_auto_download_disabled_skips_acquisition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pet_pipeline,
        "ensure_pet_detector_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not download")),
    )
    monkeypatch.setattr(
        model_bootstrap,
        "_bootstrap_dinov2_from_pinned_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not bootstrap")),
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
        model_bootstrap,
        "_bootstrap_dinov2_from_pinned_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified model must be reused")
        ),
    )
    monkeypatch.setattr(
        model_bootstrap,
        "_download_dinov2_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified model must be reused")
        ),
    )

    assert model_bootstrap.ensure_pet_model_artifacts(tmp_path) is False
