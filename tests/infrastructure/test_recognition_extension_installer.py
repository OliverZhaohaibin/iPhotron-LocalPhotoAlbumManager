import hashlib
import json
import shutil
import zipfile
from copy import deepcopy
from pathlib import Path

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
    return module.RecognitionPackage(**_identity(path.read_bytes()))


@pytest.fixture
def package(tmp_path, monkeypatch):
    target = tmp_path / "installation" / "models"
    monkeypatch.setattr(module, "installed_recognition_model_root", lambda: target)
    runtime = deepcopy(module.PET_MODEL_MANIFEST)
    monkeypatch.setattr(module, "PET_MODEL_MANIFEST", runtime)
    detector = runtime["detector"]
    embedder = runtime["embedder"]
    detector["sha256"] = _identity(b"detector")["sha256"]
    embedder["torchscript_sha256"] = _identity(b"embedder")["sha256"]
    embedder["torchscript_size"] = len(b"embedder")
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
        "version": 1,
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

    def verify_private_copy(archive, packages):
        assert archive.is_relative_to(target.parent)
        match_archive(archive, packages)
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

    def download(_package, partial, *_args):
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
