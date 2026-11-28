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


def test_linux_model_staging_is_explicit_and_verified_before_inclusion() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    build_script = (repository_root / "scripts/build_nuitka_fast.sh").read_text(
        encoding="utf-8"
    )

    assert 'if [[ -n "${IPHOTO_RECOGNITION_BUNDLE_MODELS:-}" ]]' in build_script
    assert (
        'optional_model_args+=("--include-data-dir=$MODEL_STAGE=extension/models")'
        in build_script
    )
    assert '"${optional_model_args[@]}"' in build_script
    assert "recognition_packaging.py stage-models" in build_script


def test_every_primary_build_checks_runtime_and_records_nuitka_report():
    root = Path(__file__).resolve().parents[1]
    for filename in ("build_nuitka_fast.sh", "build_nuitka_macos.sh", "build_nuitka_windows.ps1"):
        script = (root / "scripts" / filename).read_text()
        assert "--include-package=onnxruntime" in script
        assert "--include-package=usearch" in script
        assert "--nofollow-import-to=torch,torchvision,torchaudio,triton" in script
        assert "recognition_packaging.py" in script
        assert "nuitka-compilation-report.xml" in script
        assert "recognition_package.json" in script
        assert "torch/include" not in script
        assert "prune_packaged_development_files" not in script
    mac = (root / "scripts/build_nuitka_macos.sh").read_text()
    assert mac.index("repair_macos_onnx.py") < mac.index('resign_app_bundle "$APP_BUNDLE"')


def test_production_extras_do_not_depend_on_torch():
    import tomllib
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    for name, dependencies in config["project"]["optional-dependencies"].items():
        assert not any(value.split(";")[0].split(">=")[0] in {"torch", "torchvision", "torchaudio", "triton"}
                       for value in dependencies), name
