from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from iPhoto.pets import pipeline
from iPhoto.pets.errors import (
    PetInferenceError,
    PetModelUnavailableError,
    PetPipelineInvariantError,
    PetRuntimeUnavailableError,
)
from iPhoto.pets.model_contract import dinov2_metadata
from iPhoto.recognition.onnx_runtime import (
    RecognitionModelError,
    RecognitionRuntimeUnavailableError,
)


@pytest.fixture
def model(tmp_path, monkeypatch):
    path = tmp_path / "dinov2_vits14.onnx"
    path.write_bytes(b"verified ONNX fixture")
    manifest = pipeline._EMBEDDER_MANIFEST
    monkeypatch.setitem(manifest, "onnx_sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setitem(manifest, "onnx_size", path.stat().st_size)
    path.with_suffix(".onnx.metadata.json").write_text(
        json.dumps(dinov2_metadata(manifest, "dinov2_vits14"))
    )
    return path


class Session:
    def get_inputs(self):
        return [SimpleNamespace(name="image", type="tensor(float)", shape=[1, 3, 224, 224])]

    def get_outputs(self):
        return [SimpleNamespace(name="embedding", type="tensor(float)", shape=[1, 384])]

    def get_modelmeta(self):
        return SimpleNamespace(
            custom_metadata_map={
                key: pipeline._EMBEDDER_MANIFEST[key]
                for key in ("source_repository", "source_revision", "source_sha256")
            }
        )

    def run(self, outputs, inputs):
        assert outputs == ["embedding"]
        assert inputs["image"].shape == (1, 3, 224, 224)
        assert inputs["image"].dtype == np.float32
        return [np.arange(1, 385, dtype=np.float32)[None, :]]


def embedder(path, session=None):
    owner = SimpleNamespace(
        ensure_available=lambda: None, create_session=lambda *args, **kwargs: session or Session()
    )
    return pipeline._DinoV2Embedder(
        path.parent, model_name="dinov2_vits14", runtime=owner, allow_model_download=False
    )


def test_normalized_embedding_preserves_rgb_imagenet_preprocessing(model):
    value = embedder(model).embed(Image.new("RGB", (140, 350), (100, 50, 10)))
    assert value.dtype == np.float32 and value.shape == (384,)
    assert np.linalg.norm(value) == pytest.approx(1)
    np.testing.assert_allclose(
        value,
        np.arange(1, 385, dtype=np.float32) / np.linalg.norm(np.arange(1, 385, dtype=np.float32)),
    )


@pytest.mark.parametrize("kind", ["shape", "dtype", "name", "metadata"])
def test_invalid_model_contract_is_never_treated_as_device_failure(model, kind):
    session = Session()
    if kind == "metadata":
        session.get_modelmeta = lambda: SimpleNamespace(custom_metadata_map={})
    else:
        node = session.get_inputs()[0]
        setattr(
            node,
            kind,
            {"shape": [1, 3, 256, 256], "dtype": "tensor(double)", "name": "wrong"}[kind],
        )
        if kind == "dtype":
            node.type = "tensor(double)"
        session.get_inputs = lambda: [node]
    with pytest.raises(PetPipelineInvariantError):
        embedder(model, session)


@pytest.mark.parametrize(
    "output",
    [
        np.zeros((1, 384), dtype=np.float32),
        np.full((1, 384), np.nan, dtype=np.float32),
        np.full((1, 384), np.inf, dtype=np.float32),
        np.ones((1, 383), dtype=np.float32),
        np.ones((1, 384), dtype=np.float64),
    ],
)
def test_invalid_embedding_cannot_enter_historical_index(model, output):
    session = Session()
    session.run = lambda *_: [output]
    with pytest.raises(PetPipelineInvariantError):
        embedder(model, session).embed(Image.new("RGB", (100, 200)))


def test_inference_exception_uses_existing_typed_error(model):
    session = Session()

    def fail(*_):
        raise ValueError("bad input")

    session.run = fail
    with pytest.raises(PetInferenceError, match="bad input"):
        embedder(model, session).embed(Image.new("RGB", (100, 100)))


@pytest.mark.parametrize("fault", ["hash", "metadata", "missing", "old_pt"])
def test_invalid_or_old_models_never_create_a_session(model, fault):
    if fault == "hash":
        model.write_bytes(b"corrupt ONNX fixture")
    elif fault == "metadata":
        model.with_suffix(".onnx.metadata.json").write_text("{}")
    else:
        model.unlink()
        if fault == "old_pt":
            model.with_suffix(".pt").write_bytes(b"legacy torchscript is never executed")
    owner = SimpleNamespace(
        ensure_available=lambda: None,
        create_session=lambda *_: pytest.fail("invalid model reached ORT"),
    )
    with pytest.raises(PetModelUnavailableError):
        pipeline._DinoV2Embedder(
            model.parent, model_name="dinov2_vits14", runtime=owner, allow_model_download=False
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RecognitionModelError("damaged graph"), PetModelUnavailableError),
        (RecognitionRuntimeUnavailableError("missing runtime"), PetRuntimeUnavailableError),
    ],
)
def test_runtime_failures_retain_model_and_runtime_categories(model, error, expected):
    def fail(*_, **__):
        raise error

    with pytest.raises(expected):
        pipeline._DinoV2Embedder(
            model.parent,
            model_name="dinov2_vits14",
            runtime=SimpleNamespace(ensure_available=lambda: None, create_session=fail),
        )


def test_missing_runtime_is_reported_before_missing_models_or_download(tmp_path, monkeypatch):
    from iPhoto.recognition import onnx_runtime

    def missing(_):
        raise ImportError("ORT is not installed")

    monkeypatch.setattr(onnx_runtime.importlib, "import_module", missing)
    with pytest.raises(PetRuntimeUnavailableError):
        pipeline._DinoV2Embedder(tmp_path, model_name="dinov2_vits14")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("metadata", [None, [], "text", 42, True, {"onnx_size": "wrong"}])
def test_well_formed_json_with_wrong_types_uses_model_recovery(model, metadata):
    model.with_suffix(".onnx.metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(PetModelUnavailableError) as failed:
        embedder(model)
    assert failed.value.reason == "incomplete"


def test_invalid_metadata_encoding_is_a_typed_model_failure(model):
    model.with_suffix(".onnx.metadata.json").write_bytes(b"\xff\xfe")
    with pytest.raises(PetModelUnavailableError) as failed:
        embedder(model)
    assert failed.value.reason == "incomplete"


@pytest.mark.parametrize("fault", ["empty", "json_type", "metadata_missing", "corrupt"])
def test_bad_active_embedding_directory_does_not_shadow_recovery_backup(model, tmp_path, monkeypatch, fault):
    import shutil

    relative = Path("embedding/dinov2_vits14")
    active, backup = tmp_path / "active", tmp_path / "backup"
    good_dir, bad_dir = backup / relative, active / relative
    good_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)
    for source in (model, model.with_suffix(".onnx.metadata.json")):
        shutil.copyfile(source, good_dir / source.name)
        if fault != "empty":
            shutil.copyfile(source, bad_dir / source.name)
    if fault == "json_type":
        (bad_dir / "dinov2_vits14.onnx.metadata.json").write_text("[]")
    elif fault == "metadata_missing":
        (bad_dir / "dinov2_vits14.onnx.metadata.json").unlink()
    elif fault == "corrupt":
        (bad_dir / model.name).write_bytes(b"bad ONNX")
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pipeline, "pet_model_search_roots", lambda: (active, backup))
    monkeypatch.setattr(pipeline, "user_pet_model_cache_dir", lambda: tmp_path / "cache")
    assert pipeline.resolve_pet_model_path(relative, directory=True) == good_dir
    assert (good_dir / model.name).read_bytes() == model.read_bytes()


@pytest.mark.parametrize("section", ["root", "detector", "embedder"])
@pytest.mark.parametrize("value", [None, [], "text", 17, True])
def test_manifest_json_types_raise_contract_error(monkeypatch, section, value):
    from copy import deepcopy
    from iPhoto.pets import model_contract

    manifest = deepcopy(model_contract.PET_MODEL_MANIFEST)
    if section == "root":
        manifest = value
    else:
        manifest[section] = value
    manifest_path = Path(model_contract.__file__).with_name("model_manifest.json")
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw:
                        json.dumps(manifest) if self == manifest_path else original(self, *a, **kw))
    with pytest.raises(PetPipelineInvariantError):
        model_contract._load_pet_model_manifest()


@pytest.mark.parametrize("component", ["detector", "embedder"])
@pytest.mark.parametrize("failure,expected", [
    (RecognitionRuntimeUnavailableError("runtime stopped"), PetRuntimeUnavailableError),
    (RecognitionModelError("model contract"), PetModelUnavailableError),
])
def test_inference_preserves_terminal_error_categories(model, component, failure, expected):
    session = Session()

    def fail(*_):
        raise failure

    session.run = fail
    if component == "embedder":
        value = embedder(model, session)
        infer = value.embed
    else:
        value = pipeline._YoloxOnnxPetDetector.__new__(pipeline._YoloxOnnxPetDetector)
        value._session = session
        value._input_name = "image"
        value._input_size = (416, 416)
        infer = value._detect_single_image
    with pytest.raises(expected):
        infer(Image.new("RGB", (128, 128)))
