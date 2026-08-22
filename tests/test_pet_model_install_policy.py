import errno
from pathlib import Path

import pytest

from iPhoto.pets import model_bootstrap
from iPhoto.pets import pipeline as pet_pipeline


def test_macos_app_bundle_is_never_probed_as_install_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled = (
        tmp_path
        / "iPhotron.app"
        / "Contents"
        / "MacOS"
        / "extension"
        / "models"
        / "pets"
    )
    cache = tmp_path / "cache" / "pets"
    probes: list[Path] = []

    monkeypatch.setattr(model_bootstrap.sys, "platform", "darwin")
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    def _probe(path: Path) -> bool:
        probes.append(Path(path))
        return True

    monkeypatch.setattr(model_bootstrap, "_directory_is_writable_for_install", _probe)

    assert model_bootstrap._default_install_root() == cache
    assert probes == [cache]


def test_macos_source_checkout_can_still_install_into_extension(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled = tmp_path / "checkout" / "src" / "extension" / "models" / "pets"
    cache = tmp_path / "cache" / "pets"
    probes: list[Path] = []

    monkeypatch.setattr(model_bootstrap.sys, "platform", "darwin")
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    def _probe(path: Path) -> bool:
        probes.append(Path(path))
        return True

    monkeypatch.setattr(model_bootstrap, "_directory_is_writable_for_install", _probe)

    assert model_bootstrap._default_install_root() == bundled
    assert probes == [bundled]


def test_permission_denied_probe_falls_back_to_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundled = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    probes: list[Path] = []

    monkeypatch.setattr(model_bootstrap.sys, "platform", "linux")
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    def _probe(path: Path) -> bool:
        path = Path(path)
        probes.append(path)
        return path == cache

    monkeypatch.setattr(model_bootstrap, "_directory_is_writable_for_install", _probe)

    assert model_bootstrap._default_install_root() == cache
    assert probes == [bundled, cache]


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EIO])
def test_non_permission_probe_error_does_not_fall_back(
    tmp_path: Path,
    monkeypatch,
    error_number: int,
) -> None:
    bundled = tmp_path / "extension" / "pets"
    cache = tmp_path / "cache" / "pets"
    probes: list[Path] = []

    monkeypatch.setattr(model_bootstrap.sys, "platform", "linux")
    monkeypatch.delenv("IPHOTO_PET_MODEL_DIR", raising=False)
    monkeypatch.setattr(pet_pipeline, "bundled_pet_model_dir", lambda: bundled)
    monkeypatch.setattr(pet_pipeline, "user_pet_model_cache_dir", lambda: cache)

    def _probe(path: Path) -> bool:
        path = Path(path)
        probes.append(path)
        if path == bundled:
            raise OSError(error_number, "storage probe failure")
        return True

    monkeypatch.setattr(model_bootstrap, "_directory_is_writable_for_install", _probe)

    with pytest.raises(OSError) as exc_info:
        model_bootstrap._default_install_root()

    assert exc_info.value.errno == error_number
    assert probes == [bundled]


def test_probe_classifies_only_storage_permission_errnos(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_mkdir = Path.mkdir

    def _mkdir(self: Path, *args, **kwargs):
        if self == tmp_path / "readonly":
            raise OSError(errno.EROFS, "Read-only file system")
        if self == tmp_path / "full":
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _mkdir)

    assert model_bootstrap._directory_is_writable_for_install(tmp_path / "readonly") is False
    with pytest.raises(OSError) as exc_info:
        model_bootstrap._directory_is_writable_for_install(tmp_path / "full")
    assert exc_info.value.errno == errno.ENOSPC
