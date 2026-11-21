from __future__ import annotations

from pathlib import Path


def test_pet_model_manifest_is_included_by_all_packaged_builds() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    expected_target = "iPhoto/pets/model_manifest.json"
    build_scripts = (
        repository_root / "scripts" / "build_nuitka_fast.sh",
        repository_root / "scripts" / "build_nuitka_macos.sh",
        repository_root / "scripts" / "build_nuitka_windows.ps1",
    )

    for build_script in build_scripts:
        contents = build_script.read_text(encoding="utf-8").replace("\\", "/")
        assert "iPhoto/pets/model_manifest.json" in contents, build_script
        assert expected_target in contents, build_script

    pyproject = (repository_root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"iPhoto" = [' in pyproject
    assert '"**/*.json"' in pyproject


def test_linux_model_staging_is_optional_and_conditionally_included() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    build_script = (repository_root / "scripts/build_nuitka_fast.sh").read_text(
        encoding="utf-8"
    )

    assert 'if [[ -d "$ROOT_DIR/src/extension/models" ]]' in build_script
    assert (
        'optional_model_args+=("--include-data-dir=src/extension/models=extension/models")'
        in build_script
    )
    assert '"${optional_model_args[@]}"' in build_script
