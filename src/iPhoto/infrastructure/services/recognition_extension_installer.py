"""Verified and recoverable offline installation of the Recognition extension."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from iPhoto.application.ports.extension_install import ExtensionInstallError, ExtensionProgress
from iPhoto.application.ports.recognition_extension import (
    RecognitionExtensionRequest,
    RecognitionExtensionResult,
)
from iPhoto.people.model_paths import BUFFALO_S_FILES
from iPhoto.pets.model_contract import PET_MODEL_MANIFEST, dinov2_metadata
from iPhoto.utils.recognition_model_paths import (
    installed_recognition_model_root,
    previous_recognition_model_root,
)

from .extension_archives import (
    cached_download,
    check_member_path,
    copied_archive,
    download_archive,
    extract_zip,
    file_digest,
    install_failure,
    install_lock,
    match_archive,
    probe_target,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecognitionPackage:
    filename: str = "iphotron-recognition-models-v1.zip"
    size: int = 100497212
    sha256: str = "73819048dcf2d9352758a544a751be67512c18e0be458c295551b7142e91fa28"

    @property
    def url(self) -> str:
        return (
            "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
            f"releases/download/pet-models-v1/{self.filename}"
        )


class RecognitionExtensionInstaller:
    def __init__(self, package: RecognitionPackage = RecognitionPackage()):
        self._package = package

    @property
    def download_url(self) -> str:
        return self._package.url

    def execute(
        self, request: RecognitionExtensionRequest, progress: ExtensionProgress
    ) -> RecognitionExtensionResult:
        target = installed_recognition_model_root()
        stage = "prepare"
        try:
            probe_target(target.parent)
            with install_lock(target.parent / ".recognition-install.lock"):
                backup = previous_recognition_model_root(target)
                if not target.exists() and backup.exists():
                    backup.replace(target)

                if request.local_archive_path is not None:
                    stage = "verify"
                    progress(0, 0, "Validating recognition extension…")
                    with copied_archive(
                        request.local_archive_path, target.parent, prefix=".recognition-import-"
                    ) as archive:
                        match_archive(archive, (self._package,))
                        stage = "extract"
                        self._install_archive(archive, target, backup, progress)
                else:
                    stage = "download"
                    archive = cached_download(
                        target.parent,
                        self._package,
                        lambda partial: download_archive(
                            self._package,
                            partial,
                            progress,
                            "Downloading recognition extension…",
                            "iPhotron-recognition-installer/1",
                        ),
                        lambda path: match_archive(path, (self._package,), stage="download"),
                    )
                    stage = "extract"
                    self._install_archive(archive, target, backup, progress)
                    try:
                        archive.unlink(missing_ok=True)
                    except OSError:
                        _LOG.warning("Could not remove cached recognition extension %s", archive)
            return RecognitionExtensionResult(target)
        except ExtensionInstallError:
            raise
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            raise install_failure(exc, stage, f"install_root={target}") from exc

    def _install_archive(
        self, archive: Path, target: Path, backup: Path, progress: ExtensionProgress
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".recognition-stage-", dir=target.parent, ignore_cleanup_errors=True
        ) as staging:
            models = Path(staging) / "models"
            models.mkdir()
            progress(0, 0, "Extracting recognition extension…")
            extract_zip(archive, models)
            progress(0, 0, "Validating recognition extension…")
            self._validate_models(models)

            if target.exists():
                try:
                    if file_digest(target / "manifest.json") != file_digest(models / "manifest.json"):
                        raise ExtensionInstallError("integrity", "verify")
                    self._validate_models(target)
                except (OSError, ExtensionInstallError):
                    pass
                else:
                    return  # The same complete version is already installed.

            progress(0, 0, "Installing recognition extension…")
            try:
                backup.parent.mkdir(exist_ok=True)
                if target.exists():
                    # If a previous promotion was interrupted, preserve its
                    # backup while discarding the incomplete active directory.
                    displaced = models.with_name("displaced") if backup.exists() else backup
                    target.replace(displaced)
                try:
                    models.replace(target)
                except OSError:
                    if backup.exists():
                        backup.replace(target)
                    raise
            except OSError as exc:
                raise install_failure(exc, "activate", f"install_root={target}") from exc

            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _validate_models(root: Path) -> None:
        detector = PET_MODEL_MANIFEST["detector"]
        embedder = PET_MODEL_MANIFEST["embedder"]
        detector_path = f"pets/{detector['filename']}"
        embedding_path = f"pets/{embedder['filename']}"
        metadata_path = f"{embedding_path}.metadata.json"
        people_paths = {f"buffalo_s/{name}" for name in BUFFALO_S_FILES}
        pets_paths = {detector_path, embedding_path, metadata_path}
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            files = manifest["files"]
            components = manifest["components"]
            if (
                manifest["package"] != "iphotron-recognition-models"
                or manifest["schema_version"] != 1
                or manifest["version"] != 1
                or set(components["people"]["files"]) != people_paths
                or set(components["pets"]["files"]) != pets_paths
                or not (people_paths | pets_paths) <= files.keys()
            ):
                raise ExtensionInstallError("incomplete", "verify")

            for name, identity in files.items():
                check_member_path(name)
                path = root / name
                if not path.is_file():
                    raise ExtensionInstallError("incomplete", "verify")
                if path.stat().st_size != identity["size"] or file_digest(path) != identity["sha256"]:
                    raise ExtensionInstallError("integrity", "verify")

            metadata = json.loads((root / metadata_path).read_text(encoding="utf-8"))
            if (
                files[detector_path]["sha256"] != detector["sha256"]
                or not 0 < files[detector_path]["size"] <= detector["max_bytes"]
                or files[embedding_path]["size"] != embedder["torchscript_size"]
                or files[embedding_path]["sha256"] != embedder["torchscript_sha256"]
                or metadata != dinov2_metadata(embedder, embedder["model_name"])
            ):
                raise ExtensionInstallError("integrity", "verify")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ExtensionInstallError("incomplete", "verify") from exc
