from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packaging = load_tool("recognition_packaging")
repair = load_tool("repair_macos_onnx")


@pytest.mark.parametrize(
    "bad", ["torch", "torchvision", "torchaudio", "triton", "nvidia-cudnn-cu12"]
)
def test_polluted_build_environment_is_rejected(monkeypatch, bad):
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version="1", requires=[])
        for name in ("onnxruntime", bad)
    ]
    monkeypatch.setattr(packaging.importlib.metadata, "distributions", lambda: distributions)
    with pytest.raises(ValueError, match="isolated"):
        packaging.environment_report("cpu")


@pytest.mark.parametrize("second", ["onnxruntime-gpu", "custom-provider-wheel"])
def test_multiple_distributions_sharing_ort_namespace_are_rejected(monkeypatch, second):
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version="1", requires=[])
        for name in ("onnxruntime", second)
    ]
    distributions[1].files = ["onnxruntime/__init__.py"]
    monkeypatch.setattr(packaging.importlib.metadata, "distributions", lambda: distributions)
    with pytest.raises(ValueError, match="exactly"):
        packaging.environment_report("cuda")


def test_build_preflight_requires_both_recognition_contexts(monkeypatch):
    packages = [SimpleNamespace(metadata={"Name": "onnxruntime"}, version="1", requires=[])]
    monkeypatch.setattr(packaging.importlib.metadata, "distributions", lambda: packages)
    with pytest.raises(ValueError, match="Incomplete recognition"):
        packaging.environment_report("cpu", require_complete=True)


@pytest.mark.parametrize(
    "filename",
    [
        "libtorch_cpu.dylib",
        "torchvision/_C.so",
        "torch/include/file.h",
        "onnxruntime/capi/libonnxruntime_providers_cuda.so",
        "extension/models/pets/model.pt",
    ],
)
def test_forbidden_native_payloads_fail_instead_of_being_deleted(tmp_path, filename):
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"forbidden")
    with pytest.raises(ValueError, match="Forbidden"):
        packaging.artifact_report(tmp_path, "cpu")
    assert target.read_bytes() == b"forbidden"


def test_artifact_inventory_records_actual_component_bytes(tmp_path):
    files = {
        "onnxruntime/capi/onnxruntime_pybind11_state.so": b"ort",
        "extension/models/model.onnx": b"model",
    }
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    report = packaging.artifact_report(tmp_path, "cpu")
    assert report["standalone_bytes"] == 8
    assert report["component_bytes"] == {"onnxruntime": 3, "models": 5}
    assert report["torch_free"]


def test_staging_rejects_unverified_models_without_overwriting_input(tmp_path):
    contract = json.loads((ROOT / "src/iPhoto/recognition/recognition_package.json").read_text())
    source, target = tmp_path / "source", tmp_path / "destination"
    for name in contract["files"]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="Invalid"):
        packaging.stage_models(source, target)
    assert not target.exists()
    assert all(path.read_bytes() == b"wrong model" for path in source.rglob("*") if path.is_file())


def test_macos_repair_resolves_collected_libraries_and_signs_after_rewrite(tmp_path):
    bundle = tmp_path / "entrypoint.app"
    binding = bundle / "Contents/MacOS/onnxruntime/capi/onnxruntime_pybind11_state.so"
    binding.parent.mkdir(parents=True)
    binding.write_bytes(b"Mach-O fixture")
    library = bundle / "Contents/MacOS/libonnxruntime.1.dylib"
    library.write_bytes(b"Mach-O fixture")
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == "otool":
            deps = "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
            if args[-1] == str(binding):
                deps += "\t/build/venv/libonnxruntime.1.dylib (compatibility version 1.0.0)\n"
            return f"{args[-1]}:\n{deps}"
        return ""

    repair.repair(bundle, runner=runner)
    changes = [call for call in calls if call[0] == "install_name_tool"]
    assert changes == [
        [
            "install_name_tool",
            "-change",
            "/build/venv/libonnxruntime.1.dylib",
            "@loader_path/../../libonnxruntime.1.dylib",
            str(binding),
        ]
    ]
    assert calls.index(changes[0]) < calls.index(
        ["codesign", "--force", "--sign", "-", str(binding)]
    )


def test_macos_missing_provider_dependency_fails_the_build(tmp_path):
    binary = tmp_path / "Contents/MacOS/libonnxruntime.dylib"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"Mach-O fixture")
    with pytest.raises(RuntimeError, match="Unresolved"):
        repair.repair(
            tmp_path,
            runner=lambda _: "binary:\n\t@rpath/missing.dylib (compatibility version 1.0.0)\n",
        )
