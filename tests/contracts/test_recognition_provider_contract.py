"""Real provider/legacy-space qualification; simulations cannot enable it."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from iPhoto.pets.pipeline import (
    DEFAULT_PET_DISTANCE_THRESHOLD,
    PET_EMBEDDING_PIPELINE_VERSION,
    DetectedAssetPets,
    _cluster_embeddings_bounded_single_link,
    _DinoV2Embedder,
)

pytestmark = pytest.mark.dinov2_model_contract
PROVIDERS = [
    "CPUExecutionProvider",
    "CoreMLExecutionProvider",
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "OpenVINOExecutionProvider",
]


@pytest.fixture
def legacy_and_provider_embeddings(request):
    if os.environ.get("IPHOTO_RUN_DINOV2_MODEL_CONTRACT") != "1":
        pytest.skip("requires verified ONNX model and frozen legacy reference tensors")
    provider = request.param
    enabled = {
        "CPUExecutionProvider",
        *os.environ.get("IPHOTO_DINOV2_CONTRACT_PROVIDERS", "").split(","),
    }
    if provider not in enabled:
        pytest.skip(f"{provider}: Pending Manual Validation on real target hardware")
    import onnxruntime

    assert provider in onnxruntime.get_available_providers(), (
        f"Requested provider unavailable: {provider}"
    )
    root = Path(os.environ["IPHOTO_DINOV2_CONTRACT_DIR"])
    model = _DinoV2Embedder(
        root, model_name="dinov2_vits14", allow_model_download=False, execution_providers=[provider]
    )
    try:
        with np.load(root / "torchscript-reference.npz", allow_pickle=False) as reference:
            old = reference["raw_outputs"][:, 0, :]
            new = np.stack(
                [
                    model._session.run(["embedding"], {"image": tensor})[0][0]
                    for tensor in reference["inputs"]
                ]
            )
        assert provider in model._session.get_providers()
        assert model._session.fallback_reason is None, (
            "CPU fallback cannot qualify another provider"
        )
        assert new.dtype == np.float32 and new.shape == old.shape and np.isfinite(new).all()
        old = old / np.linalg.norm(old, axis=1, keepdims=True)
        new = new / np.linalg.norm(new, axis=1, keepdims=True)
        yield old, new
    finally:
        model._runtime.close()


@pytest.mark.parametrize("legacy_and_provider_embeddings", PROVIDERS, indirect=True)
def test_provider_numerics_and_mixed_historical_cluster_decisions(legacy_and_provider_embeddings):
    old, new = legacy_and_provider_embeddings
    assert np.min(np.sum(old.astype(np.float64) * new.astype(np.float64), axis=1)) >= 0.9999
    # Qualification tolerance only; production thresholds/merge rules are fixed.
    np.testing.assert_allclose(old @ old.T, new @ new.T, rtol=0, atol=1e-3)
    baseline = np.concatenate([old, old])
    for values in (np.concatenate([old, new]), np.concatenate([new, new])):
        for threshold in (0.42, 0.63):
            kwargs = {"compatibility_keys": ["cat"] * len(values), "distance_threshold": threshold}
            before = _cluster_embeddings_bounded_single_link(baseline, **kwargs)
            after = _cluster_embeddings_bounded_single_link(values, **kwargs)
            assert np.array_equal(before[:, None] == before, after[:, None] == after)


@pytest.mark.parametrize("legacy_and_provider_embeddings", PROVIDERS, indirect=True)
def test_mixed_embeddings_preserve_persisted_generation_and_identity_choices(
    tmp_path, legacy_and_provider_embeddings
):
    from iPhoto.bootstrap.library_session import create_headless_library_session
    from iPhoto.cache.index_store import get_global_repository
    from iPhoto.pets.records import PetDetectionRecord, PetRecord
    from iPhoto.pets.repository_utils import utc_now_iso

    old, new = legacy_and_provider_embeddings
    assert PET_EMBEDDING_PIPELINE_VERSION == "dinov2-vits14-imagenet-normalized-v1"
    timestamp = utc_now_iso()

    def detection(number, vector):
        return PetDetectionRecord(
            detection_id=f"detection-{number}",
            pet_key=f"v2:key-{number}",
            asset_id=f"asset-{number}",
            asset_rel=f"photo-{number}.jpg",
            box_x=0,
            box_y=0,
            box_w=80,
            box_h=80,
            confidence=0.99,
            embedding=vector,
            embedding_dim=384,
            embedding_model="dinov2_vits14",
            detector_model="yolox_nano_coco.onnx",
            thumbnail_path=None,
            pet_id="historical-pet",
            detected_at=timestamp,
            image_width=128,
            image_height=128,
            species_label="cat",
            embedding_pipeline_version=PET_EMBEDDING_PIPELINE_VERSION,
        )

    historical = [detection(number, old[0]) for number in range(3)]
    fresh = replace(detection(3, new[0]), pet_id=None)
    assets = get_global_repository(tmp_path)
    assets.write_rows(
        [
            {
                "id": item.asset_id,
                "rel": item.asset_rel,
                "media_type": 0,
                "face_status": "done",
                "pet_status": "pending" if item.asset_id == fresh.asset_id else "done",
            }
            for item in [*historical, fresh]
        ]
    )
    session = create_headless_library_session(tmp_path)
    try:
        service = session.pets
        repository = service.repository()
        repository.replace_all(
            historical,
            [
                PetRecord(
                    pet_id="historical-pet",
                    name=None,
                    key_detection_id=historical[0].detection_id,
                    detection_count=3,
                    center_embedding=old[0],
                    embedding_dim=384,
                    created_at=timestamp,
                    updated_at=timestamp,
                    sample_count=3,
                    profile_state="stable",
                    species_label="cat",
                    embedding_pipeline_version=PET_EMBEDDING_PIPELINE_VERSION,
                )
            ],
        )
        repository.activate_embedding_generation(
            generation_id=0,
            embedding_pipeline_version=PET_EMBEDDING_PIPELINE_VERSION,
            embedding_dimension=384,
        )
        assert repository.rename_pet("historical-pet", "Miso")
        assert repository.set_pet_cover("historical-pet", historical[1].detection_id)
        assert repository.set_pet_hidden("historical-pet", True)
        assert session.people.set_identity_card_order(("pet:historical-pet",)) == (
            "pet:historical-pet",
        )
        state = repository.state_repository
        before_cover = state.get_cover("historical-pet")
        before_profiles = {profile.pet_id for profile in state.get_identity_profiles()}
        event = service.coordinator.submit_detected_batch(
            [DetectedAssetPets(fresh.asset_id, fresh.asset_rel, [fresh])],
            distance_threshold=DEFAULT_PET_DISTANCE_THRESHOLD,
        )
        assert event is not None
        assert {item.pet_id for item in repository.get_all_detections()} == {"historical-pet"}
        assert {item.generation_id for item in repository.get_all_detections()} == {0}
        assert {item.embedding_pipeline_version for item in repository.get_all_detections()} == {
            PET_EMBEDDING_PIPELINE_VERSION
        }
        assert state.get_cover("historical-pet") == before_cover
        assert {profile.pet_id for profile in state.get_identity_profiles()} == before_profiles
        assert state.get_merge_redirect_map() == {}
    finally:
        session.shutdown()

    reopened = create_headless_library_session(tmp_path)
    try:
        repository = reopened.pets.repository()
        summary = repository.get_pet_summaries(include_hidden=True)[0]
        assert summary.pet_id == "historical-pet" and summary.name == "Miso" and summary.is_hidden
        assert repository.state_repository.get_cover("historical-pet") == before_cover
        assert reopened.people.get_identity_card_order() == ("pet:historical-pet",)
        rows = reopened.pets.asset_repository.get_rows_by_ids(
            [item.asset_id for item in [*historical, fresh]]
        )
        assert all(row["pet_status"] == "done" for row in rows.values())
    finally:
        reopened.shutdown()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "relative,shape,embedding",
    [
        ("buffalo_s/det_500m.onnx", (1, 3, 640, 640), False),
        ("buffalo_s/w600k_mbf.onnx", (1, 3, 112, 112), True),
        ("pets/detector/yolox_nano_coco.onnx", (1, 3, 416, 416), False),
    ],
)
def test_people_and_detector_provider_output_compatibility(provider, relative, shape, embedding):
    import hashlib
    import json
    from iPhoto.recognition.onnx_runtime import RecognitionOnnxRuntime, RecognitionRuntimePolicy

    root = os.environ.get("IPHOTO_RECOGNITION_CONTRACT_MODEL_ROOT")
    if not root:
        pytest.skip("requires the verified combined model root; no automatic download")
    enabled = {
        "CPUExecutionProvider",
        *os.environ.get("IPHOTO_DINOV2_CONTRACT_PROVIDERS", "").split(","),
    }
    if provider not in enabled:
        pytest.skip(f"{provider}: Pending Manual Validation on real target hardware")
    import onnxruntime

    assert provider in onnxruntime.get_available_providers()
    contract = json.loads(
        (
            Path(__file__).resolve().parents[2] / "src/iPhoto/recognition/recognition_package.json"
        ).read_text()
    )
    model = Path(root) / relative
    assert model.stat().st_size == contract["files"][relative]["size"]
    assert hashlib.sha256(model.read_bytes()).hexdigest() == contract["files"][relative]["sha256"]
    owner = RecognitionOnnxRuntime(policy=RecognitionRuntimePolicy(2))
    try:
        cpu = owner.create_session(model, providers=["CPUExecutionProvider"])
        candidate = owner.create_session(model, providers=[provider])
        tensor = np.random.default_rng(953).uniform(-1, 1, shape).astype(np.float32)
        if "yolox" in relative:
            tensor = (tensor + 1) * np.float32(127.5)
        before = cpu.run(None, {cpu.get_inputs()[0].name: tensor})
        after = candidate.run(None, {candidate.get_inputs()[0].name: tensor})
        assert provider in candidate.get_providers() and candidate.fallback_reason is None
        assert len(before) == len(after)
        for old, new in zip(before, after, strict=True):
            assert old.shape == new.shape and old.dtype == new.dtype and np.isfinite(new).all()
            if embedding:
                old = old / np.linalg.norm(old, axis=-1, keepdims=True)
                new = new / np.linalg.norm(new, axis=-1, keepdims=True)
                assert (
                    np.min(np.sum(old.astype(np.float64) * new.astype(np.float64), axis=-1))
                    >= 0.9999
                )
            else:
                np.testing.assert_allclose(old, new, rtol=1e-3, atol=1e-3)
    finally:
        owner.close()
