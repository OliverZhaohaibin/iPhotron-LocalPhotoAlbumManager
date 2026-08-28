from __future__ import annotations

import errno
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.pets import pipeline as pet_pipeline

pet_impl = pet_pipeline._impl

DETECTOR_RELATIVE = Path("detector") / "yolox_nano_coco.onnx"
EMBEDDER_RELATIVE = Path("embedding") / "dinov2_vits14"
_FAKE_DETECTOR_BYTES = b"detector"
_FAKE_DETECTOR_SHA256 = hashlib.sha256(_FAKE_DETECTOR_BYTES).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_detector(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FAKE_DETECTOR_BYTES)
    return path


def _invalid_detector(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"invalid")


@pytest.fixture(autouse=True)
def _clear_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)


@pytest.fixture
def _fake_detector_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pet_impl,
        "DEFAULT_PET_DETECTOR_MODEL_SHA256",
        _FAKE_DETECTOR_SHA256,
    )


class TestStoragePolicy:
    def test_extension_first_lookup_without_writable_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_detector_manifest: None,
    ) -> None:
        bundled = tmp_path / "extension"
        cache = tmp_path / "cache"
        bundled_model = _valid_detector(bundled / DETECTOR_RELATIVE)
        _valid_detector(cache / DETECTOR_RELATIVE)

        monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_impl, "user_pet_model_cache_dir", lambda: cache)
        probed: list[Path] = []

        def record_probe(path: Path) -> bool:
            probed.append(path)
            return True

        monkeypatch.setattr(pet_impl, "_directory_is_writable", record_probe)
        resolved = pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE)
        assert resolved == bundled_model
        assert not probed

    def test_cache_lookup_falls_back_after_invalid_bundled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_detector_manifest: None,
    ) -> None:
        bundled = tmp_path / "extension"
        cache = tmp_path / "cache"
        _invalid_detector(bundled / DETECTOR_RELATIVE)
        model = _valid_detector(cache / DETECTOR_RELATIVE)
        monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_impl, "user_pet_model_cache_dir", lambda: cache)
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == model
        assert (bundled / DETECTOR_RELATIVE).exists()

    def test_missing_prefers_writable_extension(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "extension"
        monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(
            pet_impl,
            "_directory_is_writable",
            lambda path: path == bundled,
        )
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == bundled / DETECTOR_RELATIVE

    def test_macos_app_installs_to_cache_without_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "iPhoto.app" / "Contents" / "Resources" / "models"
        cache = tmp_path / "cache"
        monkeypatch.setattr(pet_pipeline.sys, "platform", "darwin")
        monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_impl, "user_pet_model_cache_dir", lambda: cache)

        def fail_probe(_path: Path) -> bool:
            raise AssertionError("packaged app bundles must not be probed")

        monkeypatch.setattr(pet_impl, "_directory_is_writable", fail_probe)
        assert pet_pipeline.pet_model_install_root() == cache
        assert pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE) == cache / DETECTOR_RELATIVE

    @pytest.mark.parametrize("errno_value", [errno.EACCES, errno.EPERM, errno.EROFS])
    def test_permission_errors_are_typed_for_storage_fallback(
        self,
        errno_value: int,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(pet_pipeline._ModelStoragePermissionError):
            pet_pipeline._raise_if_model_storage_error(OSError(errno_value, "denied"), tmp_path)

    @pytest.mark.parametrize("errno_value", [errno.ENOSPC, errno.EIO])
    def test_non_permission_errors_fail_without_retry(
        self,
        errno_value: int,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(OSError) as raised:
            pet_pipeline._raise_if_model_storage_error(OSError(errno_value, "failed"), tmp_path)
        assert raised.value.errno == errno_value
        assert type(raised.value) is not pet_pipeline._ModelStoragePermissionError

    @pytest.mark.parametrize("errno_value", [errno.EACCES, errno.EPERM, errno.EROFS])
    def test_writability_probe_returns_false_only_for_permission_errors(
        self,
        errno_value: int,
    ) -> None:
        class FailingPath:
            def mkdir(self, **_kwargs) -> None:
                raise OSError(errno_value, "denied")

        assert pet_pipeline._directory_is_writable(FailingPath()) is False

    @pytest.mark.parametrize("errno_value", [errno.ENOSPC, errno.EIO])
    def test_writability_probe_surfaces_non_permission_errors(
        self,
        errno_value: int,
    ) -> None:
        class FailingPath:
            def mkdir(self, **_kwargs) -> None:
                raise OSError(errno_value, "failed")

        with pytest.raises(OSError) as raised:
            pet_pipeline._directory_is_writable(FailingPath())
        assert raised.value.errno == errno_value

    def test_only_bundled_targets_have_user_cache_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundled = tmp_path / "extension"
        cache = tmp_path / "cache"
        target = bundled / EMBEDDER_RELATIVE / "dinov2_vits14.pt"
        monkeypatch.setattr(pet_impl, "bundled_pet_model_dir", lambda: bundled)
        monkeypatch.setattr(pet_impl, "user_pet_model_cache_dir", lambda: cache)
        assert pet_pipeline._model_storage_fallback_path(target) == (
            cache / EMBEDDER_RELATIVE / "dinov2_vits14.pt"
        )
        assert pet_pipeline._model_storage_fallback_path(cache / "other.pt") is None


class TestOverrideAuthority:
    def test_search_and_install_are_override_only(self, tmp_path, monkeypatch) -> None:
        override = tmp_path / "override"
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        assert pet_pipeline.pet_model_search_roots() == (override,)
        assert pet_pipeline.pet_model_install_root() == override
        assert (
            pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE)
            == override / DETECTOR_RELATIVE
        )
        assert pet_pipeline._model_storage_fallback_path(override / DETECTOR_RELATIVE) is None

    def test_pipeline_rejects_mismatched_model_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(tmp_path / "override"))
        pipeline_instance = pet_pipeline.PetClusterPipeline(model_root=tmp_path / "models")
        with pytest.raises(pet_pipeline.PetModelUnavailableError):
            pipeline_instance._resolve_model_path(DETECTOR_RELATIVE)

    def test_invalid_dino_override_is_repaired_in_place_without_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        override = tmp_path / "override"
        model_dir = override / EMBEDDER_RELATIVE
        model_dir.mkdir(parents=True)
        (model_dir / "dinov2_vits14.pt").write_bytes(b"legacy")
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        monkeypatch.setattr(
            pet_impl,
            "user_pet_model_cache_dir",
            lambda: (_ for _ in ()).throw(AssertionError("override must not fall back")),
        )

        assert pet_pipeline.resolve_pet_model_path(
            EMBEDDER_RELATIVE,
            directory=True,
        ) == model_dir

    def test_detector_override_permission_error_does_not_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        override = tmp_path / "override"
        target = override / DETECTOR_RELATIVE
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        calls: list[Path] = []

        def fail_download(_url: str, destination: Path, **_kwargs):
            calls.append(Path(destination))
            raise pet_pipeline._ModelStoragePermissionError(errno.EACCES, "denied")

        monkeypatch.setattr(pet_impl, "_download_file", fail_download)
        with pytest.raises(RuntimeError, match="not writable"):
            pet_pipeline.ensure_pet_detector_model(target)
        assert calls == [target]

    def test_invalid_detector_override_is_repaired_in_place_without_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_detector_manifest: None,
    ) -> None:
        override = tmp_path / "override"
        target = override / DETECTOR_RELATIVE
        _invalid_detector(target)
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        monkeypatch.setattr(
            pet_impl,
            "user_pet_model_cache_dir",
            lambda: (_ for _ in ()).throw(AssertionError("override must not fall back")),
        )
        downloads: list[Path] = []

        def repair(_url: str, destination: Path, **_kwargs) -> Path:
            downloads.append(Path(destination))
            Path(destination).write_bytes(_FAKE_DETECTOR_BYTES)
            return Path(destination)

        monkeypatch.setattr(pet_impl, "_download_file", repair)

        resolved = pet_pipeline.resolve_pet_model_path(DETECTOR_RELATIVE)
        assert resolved == target
        assert pet_pipeline.ensure_pet_detector_model(resolved) == target
        assert downloads == [target]
        assert target.read_bytes() == _FAKE_DETECTOR_BYTES

    def test_invalid_detector_is_preserved_when_download_is_disabled(
        self,
        tmp_path: Path,
        _fake_detector_manifest: None,
    ) -> None:
        target = tmp_path / "override" / DETECTOR_RELATIVE
        _invalid_detector(target)

        with pytest.raises(RuntimeError, match="SHA-256"):
            pet_pipeline.ensure_pet_detector_model(
                target,
                allow_model_download=False,
            )

        assert target.read_bytes() == b"invalid"

    def test_detector_repair_non_permission_unlink_error_does_not_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _fake_detector_manifest: None,
    ) -> None:
        target = tmp_path / "override" / DETECTOR_RELATIVE
        _invalid_detector(target)
        real_unlink = Path.unlink

        def fail_unlink(self: Path, *args, **kwargs):
            if self == target:
                raise OSError(errno.ENOSPC, "full")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        monkeypatch.setattr(
            pet_impl,
            "_download_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("non-permission repair errors must not download")
            ),
        )

        with pytest.raises(OSError) as raised:
            pet_pipeline.ensure_pet_detector_model(target)
        assert raised.value.errno == errno.ENOSPC

    def test_dino_override_permission_error_does_not_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        override = tmp_path / "override"
        target = override / EMBEDDER_RELATIVE / "dinov2_vits14.pt"
        monkeypatch.setenv("IPHOTO_PET_MODEL_DIR", str(override))
        embedder = pet_pipeline._DinoV2Embedder.__new__(pet_pipeline._DinoV2Embedder)
        calls: list[Path] = []

        def fail_build(path: Path):
            calls.append(Path(path))
            raise pet_pipeline._ModelStoragePermissionError(errno.EACCES, "denied")

        embedder._build_dinov2_cache = fail_build
        with pytest.raises(pet_pipeline.PetModelUnavailableError, match="not writable"):
            embedder._download_dinov2_model(target)
        assert calls == [target]


class TestDownloadsAndMetadata:
    def test_embedder_rejects_an_unpinned_torch_runtime(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(__version__="2.11.0"))
        with pytest.raises(
            pet_pipeline.PetRuntimeUnavailableError,
            match="requires torch==2.12.1",
        ):
            pet_pipeline._DinoV2Embedder(
                tmp_path,
                model_name="dinov2_vits14",
            )

    def test_exact_size_is_enforced(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "model.bin"
        payload = b"exact-size"

        class Response:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int):
                value, self.payload = self.payload, b""
                return value

        monkeypatch.setattr(
            pet_pipeline.request,
            "urlopen",
            lambda *_args, **_kwargs: Response(payload),
        )
        pet_pipeline._download_file(
            "https://example.test/model.bin",
            target,
            label="test model",
            expected_sha256=_sha256_target(payload),
            max_bytes=100,
            exact_size=len(payload),
        )
        assert target.read_bytes() == payload

        short = payload[:-1]
        monkeypatch.setattr(
            pet_pipeline.request,
            "urlopen",
            lambda *_args, **_kwargs: ShortResponse(short),
        )
        with pytest.raises(RuntimeError, match="wrong file size"):
            pet_pipeline._download_file(
                "https://example.test/model.bin",
                target.with_suffix(".short"),
                label="test model",
                expected_sha256=_sha256_target(short),
                max_bytes=100,
                exact_size=len(payload),
            )

    def test_download_preserves_permission_error_for_storage_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_temp_dir(*_args, **_kwargs):
            raise OSError(errno.EACCES, "denied")

        monkeypatch.setattr(pet_pipeline.tempfile, "TemporaryDirectory", fail_temp_dir)
        with pytest.raises(pet_pipeline._ModelStoragePermissionError):
            pet_pipeline._download_file(
                "https://example.test/model.bin",
                tmp_path / "model.bin",
                label="test model",
                expected_sha256="0" * 64,
                max_bytes=100,
            )

    def test_manifest_dino_contract_uses_official_checkpoint(self) -> None:
        manifest = pet_pipeline._EMBEDDER_MANIFEST
        assert manifest["weights_url"] == (
            "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/"
            "dinov2_vits14_pretrain.pth"
        )
        assert manifest["weights_sha256"] == (
            "b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9"
        )
        assert manifest["weights_size"] == 88283115
        assert manifest["artifact_kind"] == "release_torchscript"
        assert manifest["release_tag"] == "pet-models-v1"
        assert manifest["cache_schema_version"] == 2
        assert manifest["producer_torch_version"] == "2.12.1"
        assert manifest["producer_torchvision_version"] == "0.27.1"
        assert manifest["torchscript_url"].endswith(
            "/releases/download/pet-models-v1/dinov2_vits14.pt"
        )

    def test_legacy_derived_metadata_is_rejected(self, tmp_path: Path) -> None:
        content = b"derived-torchscript"
        metadata = {
            "artifact_kind": "derived_checkpoint_cache",
            "model_name": "dinov2_vits14",
            "source_repository": pet_pipeline._EMBEDDER_MANIFEST["source_repository"],
            "source_revision": pet_pipeline._DINO_SOURCE_REVISION,
            "weights_sha256": pet_pipeline._DINO_WEIGHTS_SHA256,
            "weights_size": pet_pipeline._DINO_WEIGHTS_SIZE,
            "derived_torchscript_sha256": hashlib.sha256(content).hexdigest(),
            "derived_torchscript_size": len(content),
            "input_shape": [1, 3, 224, 224],
            "output_shape": [1, 384],
        }
        model_path = tmp_path / "dinov2_vits14.pt"
        model_path.write_bytes(content)
        pet_pipeline._dinov2_metadata_path(model_path).write_text(
            hashlib_json(metadata), encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="metadata contract mismatch"):
            pet_pipeline._validate_dinov2_cache_metadata(
                model_path,
                model_name="dinov2_vits14",
            )

    def test_dino_release_download_publishes_model_and_metadata_together(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model_bytes = b"release-torchscript"
        model_name = "dinov2_vits14"
        model_path = tmp_path / EMBEDDER_RELATIVE / f"{model_name}.pt"

        class FakeTensor:
            shape = (1, 384)

        class FakeModel:
            def eval(self):
                return self

            def to(self, _device):
                return self

            def __call__(self, _example):
                return FakeTensor()

        class FakeJit:
            @staticmethod
            def load(path: str, *, map_location):
                assert Path(path).read_bytes() == model_bytes
                assert map_location in {"cpu", "test-device"}
                return FakeModel()

        class FakeNoGrad:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        fake_torch = SimpleNamespace(
            jit=FakeJit(),
            float32=object(),
            randn=lambda *_args, **_kwargs: object(),
            no_grad=lambda: FakeNoGrad(),
        )
        embedder = pet_pipeline._DinoV2Embedder.__new__(pet_pipeline._DinoV2Embedder)
        embedder._torch = fake_torch
        embedder._device = "test-device"
        embedder._model_name = model_name

        digest = hashlib.sha256(model_bytes).hexdigest()
        monkeypatch.setattr(pet_impl, "_DINO_TORCHSCRIPT_SHA256", digest)
        monkeypatch.setattr(pet_impl, "_DINO_TORCHSCRIPT_SIZE", len(model_bytes))
        monkeypatch.setitem(pet_pipeline._EMBEDDER_MANIFEST, "torchscript_sha256", digest)
        monkeypatch.setitem(
            pet_pipeline._EMBEDDER_MANIFEST,
            "torchscript_size",
            len(model_bytes),
        )

        def fake_download(url: str, destination: Path, **kwargs) -> Path:
            assert url == pet_pipeline._DINO_TORCHSCRIPT_URL
            assert kwargs["expected_sha256"] == digest
            assert kwargs["max_bytes"] == len(model_bytes)
            assert kwargs["exact_size"] == len(model_bytes)
            Path(destination).write_bytes(model_bytes)
            return Path(destination)

        monkeypatch.setattr(pet_pipeline, "_install_certifi_environment", lambda: None)
        monkeypatch.setattr(pet_pipeline, "_download_file", fake_download)

        loaded = embedder._build_dinov2_cache(model_path)
        metadata_path = pet_pipeline._dinov2_metadata_path(model_path)
        assert isinstance(loaded, FakeModel)
        assert model_path.read_bytes() == model_bytes
        assert metadata_path.is_file()
        pet_pipeline._validate_dinov2_cache_metadata(model_path, model_name=model_name)
        metadata = hashlib_json_load(metadata_path)
        assert metadata["artifact_kind"] == "release_torchscript"
        assert metadata["cache_schema_version"] == 2
        assert metadata["torchscript_sha256"] == digest
        assert metadata["torchscript_size"] == len(model_bytes)

    def test_dino_publish_rolls_back_model_when_metadata_publish_is_denied(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        candidate = tmp_path / "candidate.pt"
        candidate_metadata = tmp_path / "candidate.pt.metadata.json"
        model_path = tmp_path / "published" / "dinov2_vits14.pt"
        final_metadata = pet_pipeline._dinov2_metadata_path(model_path)
        model_path.parent.mkdir(parents=True)
        candidate.write_bytes(b"candidate")
        candidate_metadata.write_text("{}", encoding="utf-8")

        real_replace = Path.replace

        def fail_metadata_replace(self: Path, target: Path):
            if self == candidate_metadata:
                raise OSError(errno.EACCES, "denied")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", fail_metadata_replace)

        with pytest.raises(pet_pipeline._ModelStoragePermissionError):
            pet_pipeline._publish_dinov2_cache_pair(
                candidate,
                candidate_metadata,
                model_path,
            )

        assert not model_path.exists()
        assert not final_metadata.exists()


class ShortResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int):
        value, self.payload = self.payload, b""
        return value


def _sha256_target(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hashlib_json(value: dict) -> str:
    import json

    return json.dumps(value)


def hashlib_json_load(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


class TestLazyPipeline:
    def test_empty_batch_does_not_initialize_models(self, tmp_path: Path) -> None:
        pipeline_instance = pet_pipeline.PetClusterPipeline(model_root=tmp_path / "models")

        def fail_initialize():
            raise AssertionError("empty batches must not load or download models")

        pipeline_instance._ensure_detector = fail_initialize
        pipeline_instance._ensure_embedder = fail_initialize
        assert pipeline_instance.detect_pets_for_rows([], library_root=tmp_path, thumbnail_dir=tmp_path) == []
