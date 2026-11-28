import hashlib
import json
import shutil
import zipfile
from concurrent.futures import CancelledError
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest

from iPhoto.application.ports.extension_install import ExtensionInstallError
from iPhoto.application.ports.recognition_extension import RecognitionExtensionRequest
from iPhoto.infrastructure.services import recognition_extension_installer as module
from iPhoto.infrastructure.services.extension_archives import install_lock
from iPhoto.people.model_paths import BUFFALO_S_FILES
from iPhoto.pets.model_contract import dinov2_metadata
from iPhoto.utils.recognition_model_paths import previous_recognition_model_root


def _identity(content: bytes) -> dict:
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _archive(path: Path, files: dict):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return module.RecognitionPackage(**_identity(path.read_bytes()), url="https://models.example.test/models-v2.zip")


@pytest.fixture
def package(tmp_path, monkeypatch):
    target = tmp_path / "installation" / "models"
    monkeypatch.setattr(module, "installed_recognition_model_root", lambda: target)
    runtime = deepcopy(module.PET_MODEL_MANIFEST)
    monkeypatch.setattr(module, "PET_MODEL_MANIFEST", runtime)
    detector = runtime["detector"]
    embedder = runtime["embedder"]
    detector["sha256"] = _identity(b"detector")["sha256"]
    embedder["onnx_sha256"] = _identity(b"embedder")["sha256"]
    embedder["onnx_size"] = len(b"embedder")
    people = {f"buffalo_s/{name}": b"face-model" for name in BUFFALO_S_FILES}
    pets = {
        f"pets/{detector['filename']}": b"detector",
        f"pets/{embedder['filename']}": b"embedder",
        f"pets/{embedder['filename']}.metadata.json": json.dumps(
            dinov2_metadata(embedder, embedder["model_name"])
        ).encode(),
    }
    files = {**people, **pets}
    manifest = {
        "package": "iphotron-recognition-models",
        "schema_version": 1,
        "version": 2,
        "components": {"people": {"files": list(people)}, "pets": {"files": list(pets)}},
        "files": {name: _identity(content) for name, content in files.items()},
    }
    files["manifest.json"] = json.dumps(manifest)
    return tmp_path / "models.zip", target, files


def test_local_install_uses_verified_private_copy_without_network(package, monkeypatch):
    path, target, files = package
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    monkeypatch.setattr(module, "download_archive", lambda *_: pytest.fail("unexpected download"))
    match_archive = module.match_archive

    def verify_private_copy(archive, packages, **kwargs):
        assert archive.is_relative_to(target.parent)
        match_archive(archive, packages, **kwargs)
        path.write_bytes(b"changed after verification")

    monkeypatch.setattr(module, "match_archive", verify_private_copy)
    result = installer.execute(RecognitionExtensionRequest(path), lambda *_: None)

    assert result.model_root == target
    for name, content in files.items():
        if isinstance(content, bytes):
            assert (target / name).read_bytes() == content


@pytest.mark.parametrize(
    ("change", "category"),
    [("traversal", "unsafe_archive"), ("incomplete", "incomplete"), ("wrong_zip", "unknown_package")],
)
def test_install_rejects_invalid_archive(package, change, category):
    path, target, files = package
    if change == "traversal":
        files["../outside"] = b"unsafe"
    elif change == "incomplete":
        del files[next(iter(files))]
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    if change == "wrong_zip":
        _archive(path, {"extension/map.bin": b"map"})
    with pytest.raises(ExtensionInstallError) as error:
        installer.execute(RecognitionExtensionRequest(path), lambda *_: None)

    assert error.value.category == category
    assert not target.exists()
    assert not (target.parent / "outside").exists()


def test_failed_activation_restores_previous_directory_and_keeps_download(package, monkeypatch):
    path, target, files = package
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    target.mkdir(parents=True)
    (target / "previous").write_bytes(b"older installation")
    downloads = []

    def download(_package, partial, *_args, **_kwargs):
        downloads.append(partial)
        shutil.copyfile(path, partial)

    monkeypatch.setattr(module, "download_archive", download)
    replace = Path.replace

    def fail_activation(self, destination):
        if destination == target and self.parent.name.startswith(".recognition-stage-"):
            raise PermissionError("blocked promotion")
        return replace(self, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_activation)
        with pytest.raises(ExtensionInstallError, match="stage=activate"):
            installer.execute(RecognitionExtensionRequest(), lambda *_: None)

    assert (target / "previous").read_bytes() == b"older installation"
    assert len(downloads) == 1 and not downloads[0].exists()
    assert list((target.parent / ".downloads").glob("*.zip"))

    installer.execute(RecognitionExtensionRequest(), lambda *_: None)
    assert len(downloads) == 1
    assert (target / "manifest.json").is_file()


def test_install_is_serialized_and_recovers_when_previous_directory_was_renamed(package):
    path, target, files = package
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    request = RecognitionExtensionRequest(path)
    installer.execute(request, lambda *_: None)
    backup = previous_recognition_model_root(target)
    target.replace(backup)

    with install_lock(target.parent / ".recognition-install.lock"):
        with pytest.raises(ExtensionInstallError, match="category=busy"):
            installer.execute(request, lambda *_: None)
        assert backup.is_dir() and not target.exists()

    installer.execute(request, lambda *_: None)
    assert target.is_dir() and not backup.exists()


def test_cancelled_download_removes_partial_and_releases_lock(package, monkeypatch):
    path, target, files = package
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    cancel = Event()

    def download(_package, partial, *_args, check_cancelled):
        partial.write_bytes(b"partial")
        cancel.set()
        check_cancelled()

    monkeypatch.setattr(module, "download_archive", download)
    with pytest.raises(CancelledError):
        installer.execute(RecognitionExtensionRequest(), lambda *_: None, is_cancelled=cancel.is_set)

    assert not list((target.parent / ".downloads").iterdir())
    assert not target.exists()
    installer.execute(RecognitionExtensionRequest(path), lambda *_: None)
    assert target.is_dir()


def test_v1_remains_usable_for_people_and_is_untouched_by_failed_v2_upgrade(package, monkeypatch):
    from iPhoto.people import model_paths
    from iPhoto.utils import recognition_model_paths

    path, target, files = package
    # Mirror the production v1/v2 siblings under a disposable application root.
    target = target.parent / "v2" / "models"
    legacy = target.parent.parent / "v1" / "models"
    for name in BUFFALO_S_FILES:
        model = legacy / "buffalo_s" / name
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"valid legacy People ONNX")
    old_pet = legacy / "pets/embedding/dinov2_vits14/dinov2_vits14.pt"
    old_pet.parent.mkdir(parents=True)
    old_pet.write_bytes(b"legacy TorchScript is never executed")
    before = {item.relative_to(legacy): item.read_bytes() for item in legacy.rglob("*") if item.is_file()}
    monkeypatch.setattr(recognition_model_paths, "installed_recognition_model_root", lambda: target)
    monkeypatch.setattr(module, "installed_recognition_model_root", lambda: target)
    assert model_paths.resolve_face_model_dir(target / "missing") == legacy
    path.write_bytes(b"interrupted ZIP")
    with pytest.raises(ExtensionInstallError):
        module.RecognitionExtensionInstaller().execute(RecognitionExtensionRequest(path), lambda *_: None)
    assert before == {item.relative_to(legacy): item.read_bytes() for item in legacy.rglob("*") if item.is_file()}
    assert model_paths.resolve_face_model_dir(target / "missing") == legacy
    assert not target.exists()


def test_v1_archive_is_not_accepted_as_v2_even_when_outer_identity_is_trusted(package):
    path, target, files = package
    manifest = json.loads(files["manifest.json"])
    manifest["version"] = 1
    files["manifest.json"] = json.dumps(manifest)
    installer = module.RecognitionExtensionInstaller(_archive(path, files))
    with pytest.raises(ExtensionInstallError, match="incomplete"):
        installer.execute(RecognitionExtensionRequest(path), lambda *_: None)
    assert not target.exists()


def test_unpublished_release_does_not_attempt_network_or_create_fake_url(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "installed_recognition_model_root", lambda: tmp_path / "v2/models")
    monkeypatch.setattr(module, "download_archive", lambda *_args, **_kwargs: pytest.fail("unexpected network"))
    installer = module.RecognitionExtensionInstaller(module.RecognitionPackage(url=""))
    assert installer.download_url == ""
    with pytest.raises(ExtensionInstallError, match="unpublished"):
        installer.execute(RecognitionExtensionRequest(), lambda *_: None)
