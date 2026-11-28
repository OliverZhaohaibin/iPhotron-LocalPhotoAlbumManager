from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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


def test_nuitka_loads_recognition_rules_without_replacing_builtin_dlls(tmp_path):
    pytest.importorskip("nuitka")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from nuitka.Tracing import general
from nuitka.utils import Yaml

Yaml.getUserProvidedYamlFiles = lambda: [sys.argv[1]]
config = Yaml.getYamlPackageConfiguration(general, False, False)
print(json.dumps({
    "builtin": config.get("onnxruntime", "dlls"),
    "additional": config.get("onnxruntime.capi", "dlls"),
}))
""",
            str(ROOT / "packaging/recognition.nuitka-package.config.yml"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rules = json.loads(result.stdout)
    builtin_prefixes = {
        prefix
        for rule in rules["builtin"]
        for prefix in rule.get("from_filenames", {}).get("prefixes", [])
    }
    assert {"onnxruntime", "libonnxruntime"} <= builtin_prefixes
    directml = next(
        rule
        for rule in rules["additional"]
        if "DirectML" in rule.get("from_filenames", {}).get("prefixes", [])
    )
    assert directml["when"] == "win32"
    assert directml["from_filenames"].get("relative_path", ".") == "."


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


def test_artifact_inventory_records_actual_component_bytes(tmp_path, monkeypatch):
    contract = tmp_path / "contract.json"
    payload = b"model"
    contract.write_text(json.dumps({"files": {"model.onnx": {
        "size": len(payload), "sha256": packaging.hashlib.sha256(payload).hexdigest()
    }}}))
    monkeypatch.setattr(packaging, "MODEL_CONTRACT", contract)
    files = {
        "onnxruntime/capi/onnxruntime_pybind11_state.so": b"ort",
        "extension/models/model.onnx": b"model",
    }
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    report = packaging.artifact_report(tmp_path, "cpu")
    assert report["standalone_bytes"] == 8 + contract.stat().st_size
    assert report["component_bytes"]["onnxruntime"] == 3
    assert report["component_bytes"]["models"] == 5
    assert report["torch_free"]


@pytest.mark.parametrize("filename", [
    "libcuda.so.1", "cudart64_12.dll", "libcudadevrt.a", "libcufft.so.11", "libcurand.so.10",
    "libcusolver.so.11", "libcusparse.so.12", "libcusparseLt.so.0", "libcupti.so.12",
    "libnvrtc-builtins.so.12.8", "nvJitLink_120_0.dll", "libnvptxcompiler_static.a",
    "libnvfatbin.so.12", "libnppc.so.12", "nvjpeg64_12.dll", "libnvblas.so.12",
    "libcufile.so.0", "libnvToolsExt.so.1", "libnvinfer.so.10", "libnvonnxparser.so.10",
    "libnccl.so.2", "libcutensor.so.2", "libcudss.so.0", "libnvshmem.so.3",
    "libcustatevec.so.1", "libcutensornet.so.2", "libnvidia-ml.so.1", "libnvvm.so.4",
    "libnvcomp.so.4", "libnvperf_host.so", "libpcsamplingutil.so", "libnvoptix.so.1",
    "libcudbg.so.1", "libcuinj64.so.12", "kernel.cubin", "kernel.fatbin", "kernel.ptx",
])
def test_complete_cuda_native_families_are_rejected_in_cpu_payloads(tmp_path, filename):
    target = tmp_path / filename
    target.write_bytes(b"native fixture")
    with pytest.raises(ValueError, match="Forbidden"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)
    assert target.exists()
    assert not packaging.forbidden_native(filename, "cuda")


@pytest.mark.parametrize("name", ["libtorch.so", "torch_python.dll", "c10.dll", "torchvision.dll", "libtorchaudio.so", "libtriton.so"])
def test_torch_linked_from_an_innocently_named_binary_is_forbidden(name):
    assert packaging.forbidden_native(name, "cpu")
    assert packaging.forbidden_native(name, "cuda")


@pytest.mark.parametrize("magic,tool,output,expected", [
    (b"\x7fELF", "readelf", " 0x1 (NEEDED) Shared library: [libcurand.so.10]\n", "libcurand.so.10"),
    (b"\xcf\xfa\xed\xfe", "otool", "binary:\n\t@rpath/libtorch.dylib (compatibility version 0.0.0, current version 0.0.0)\n", "@rpath/libtorch.dylib"),
    (b"MZ00", "llvm-readobj", "Import {\n Name: cudnn64_9.dll\n}\nDelayImport {\n Name: nvrtc64_120_0.dll\n}\n", "cudnn64_9.dll"),
    (b"MZ00", "dumpbin", "  Image has the following dependencies:\n    cublas64_12.dll\n", "cublas64_12.dll"),
    (b"MZ00", "objdump", " DLL Name: cudart64_12.dll\n", "cudart64_12.dll"),
])
def test_actual_import_tables_expose_forbidden_dependencies(tmp_path, monkeypatch, magic, tool, output, expected):
    binary = tmp_path / "innocent.bin"
    binary.write_bytes(magic + b"native fixture")
    monkeypatch.setattr(packaging.shutil, "which", lambda name: tool if name == tool else None)
    calls = []

    def runner(args):
        calls.append(args)
        return output

    monkeypatch.setattr(packaging, "_run_inspector", runner)
    imports = packaging.native_dependencies(binary)
    assert expected in imports["dependencies"]
    if tool == "llvm-readobj":
        assert "nvrtc64_120_0.dll" in imports["dependencies"]
    assert calls and calls[0][-1] == str(binary)
    with pytest.raises(ValueError, match="innocent.bin ->"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


def test_native_dependency_audit_fails_closed_without_inspector(tmp_path, monkeypatch):
    binary = tmp_path / "binary.so"
    binary.write_bytes(b"\x7fELF fixture")
    monkeypatch.setattr(packaging.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Install readelf"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


@pytest.fixture
def pe_discovery(tmp_path, monkeypatch):
    packaging._vswhere_dumpbins.cache_clear()
    for name in ("VCToolsInstallDir", "VSINSTALLDIR", "ProgramFiles(x86)", "ProgramFiles", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(packaging.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(packaging.shutil, "which", lambda _: None)
    binary = tmp_path / "build Python.exe"
    binary.write_bytes(b"MZ00 fixture")
    yield binary
    packaging._vswhere_dumpbins.cache_clear()


def _dumpbin_fixture(root, version="14.44.123", host="Hostx64", target="x64"):
    path = root / f"VC/Tools/MSVC/{version}/bin/{host}/{target}/dumpbin.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


@pytest.mark.parametrize("variable", ["VCToolsInstallDir", "VSINSTALLDIR"])
def test_pe_audit_finds_msvc_tools_outside_path(pe_discovery, tmp_path, monkeypatch, variable):
    root = tmp_path / "Visual Studio with spaces"
    tool = _dumpbin_fixture(root)
    _dumpbin_fixture(root, version="14.50.0", host="Hostx86", target="x86")
    _dumpbin_fixture(root, version="14.9.0")
    monkeypatch.setenv(variable, str(tool.parents[3] if variable == "VCToolsInstallDir" else root))
    calls = []

    def runner(args):
        calls.append(args)
        return "    KERNEL32.dll\n    c10.dll\n"

    monkeypatch.setattr(packaging, "_run_inspector", runner)
    report = packaging.native_dependencies(pe_discovery)
    assert report["inspector_path"] == str(tool.resolve())
    assert calls == [[str(tool.resolve()), "/DEPENDENTS", str(pe_discovery)]]
    # Discovery must not weaken the existing final dependency gate.
    with pytest.raises(ValueError, match="build Python.exe -> c10.dll"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


@pytest.mark.parametrize("location", ["path", "ProgramFiles(x86)", "ProgramFiles"])
def test_pe_audit_uses_vswhere_build_tools_and_caches_discovery(
    pe_discovery, tmp_path, monkeypatch, location
):
    base = tmp_path / "Program Files with spaces"
    vswhere = base / "Microsoft Visual Studio/Installer/vswhere.exe"
    vswhere.parent.mkdir(parents=True)
    vswhere.touch()
    if location == "path":
        monkeypatch.setattr(packaging.shutil, "which", lambda name: str(vswhere) if name == "vswhere" else None)
    else:
        monkeypatch.setenv(location, str(base))
    root = tmp_path / "自定义 Visual Studio Build Tools"
    old = _dumpbin_fixture(root, version="14.9.0")
    selected = _dumpbin_fixture(root, version="14.44.123")
    other_host = _dumpbin_fixture(root, version="14.50.0", host="Hostx86", target="x86")
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == str(vswhere.resolve()):
            assert args[1:5] == ["-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"]
            assert args[-1] == "-utf8"
            return "\n".join(map(str, [old, other_host, selected])) + "\n"
        assert args == [str(selected.resolve()), "/DEPENDENTS", str(pe_discovery)]
        return "    KERNEL32.dll\n"

    monkeypatch.setattr(packaging, "_run_inspector", runner)
    for _ in range(2):
        assert packaging.native_dependencies(pe_discovery)["inspector_path"] == str(selected.resolve())
    assert sum(call[0] == str(vswhere.resolve()) for call in calls) == 1


@pytest.mark.parametrize("failure", [OSError("cannot execute"), ValueError("inspection failed")])
def test_pe_audit_continues_after_an_unusable_path_tool(pe_discovery, tmp_path, monkeypatch, failure):
    broken = tmp_path / "llvm-readobj.exe"
    tool = _dumpbin_fixture(tmp_path / "VS")
    monkeypatch.setattr(packaging.shutil, "which", lambda name: str(broken) if name == "llvm-readobj" else None)
    monkeypatch.setenv("VCToolsInstallDir", str(tool.parents[3]))

    def runner(args):
        if args[0] == str(broken):
            raise failure
        assert args[0] == str(tool)
        return "    KERNEL32.dll\n"

    assert packaging.native_dependencies(pe_discovery, runner=runner)["inspector_path"] == str(tool)


def test_environment_preflight_records_a_working_native_inspector(pe_discovery, tmp_path, monkeypatch):
    tool = _dumpbin_fixture(tmp_path / "VS")
    monkeypatch.setenv("VSINSTALLDIR", str(tmp_path / "VS"))
    monkeypatch.setattr(packaging.sys, "executable", str(pe_discovery))
    monkeypatch.setattr(packaging, "environment_report", lambda *_a, **_kw: {"torch_free": True})
    monkeypatch.setattr(packaging, "_run_inspector", lambda _: "    KERNEL32.dll\n")
    output = tmp_path / "report.json"
    assert packaging.main(["environment", "--check-native-inspector", "--output", str(output)]) == 0
    report = json.loads(output.read_text())["native_inspector"]
    assert report["inspector_path"] == str(tool)
    assert report["format"] == "PE" and report["dependencies"] == ["KERNEL32.dll"]


@pytest.mark.parametrize("unreadable", [False, True])
def test_environment_preflight_fails_closed_before_writing_report(pe_discovery, tmp_path, monkeypatch, unreadable):
    monkeypatch.setattr(packaging.sys, "executable", str(pe_discovery))
    monkeypatch.setattr(packaging, "environment_report", lambda *_a, **_kw: {"torch_free": True})
    if unreadable:
        monkeypatch.setattr(packaging.shutil, "which", lambda name: "dumpbin.exe" if name == "dumpbin" else None)
        monkeypatch.setattr(packaging, "_run_inspector", lambda _: "unrecognized output")
    output = tmp_path / "report.json"
    with pytest.raises(SystemExit) as failed:
        packaging.main(["environment", "--check-native-inspector", "--output", str(output)])
    assert failed.value.code == 2
    assert not output.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires a real Windows PE and MSVC installation")
def test_real_windows_pe_audit_without_developer_path(monkeypatch):
    if not any(
        (Path(os.environ.get(name, "")) / "Microsoft Visual Studio/Installer/vswhere.exe").is_file()
        for name in ("ProgramFiles(x86)", "ProgramFiles")
    ):
        pytest.skip("Visual Studio Installer is not installed")
    monkeypatch.setattr(packaging.shutil, "which", lambda _: None)
    monkeypatch.delenv("VCToolsInstallDir", raising=False)
    monkeypatch.delenv("VSINSTALLDIR", raising=False)
    packaging._vswhere_dumpbins.cache_clear()
    try:
        report = packaging.check_native_inspector()
        assert report["format"] == "PE" and report["dependencies"]
        assert report["inspector"].lower() == "dumpbin.exe"
        assert Path(report["inspector_path"]).is_absolute()
    finally:
        packaging._vswhere_dumpbins.cache_clear()


def test_duplicate_large_files_are_reported_without_deleting_them(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, "LARGE_FILE_BYTES", 8)
    for name in ("first.so", "second.so", "unique.so"):
        (tmp_path / name).write_bytes(b"duplicate" if name != "unique.so" else b"different")
    (tmp_path / "alias.so").symlink_to("first.so")
    report = packaging.artifact_report(tmp_path, "cpu", require_runtime=False)
    assert len(report["duplicate_large_files"]) == 1
    group = report["duplicate_large_files"][0]
    assert group["paths"] == ["first.so", "second.so"]
    assert group["duplicate_bytes"] == 9
    assert (tmp_path / "second.so").read_bytes() == b"duplicate"


@pytest.mark.parametrize("resource", ["unused.onnx", "source.pt", "torchscript-reference.npz", "training.pth", "README.txt"])
def test_unnecessary_model_resources_are_rejected(tmp_path, resource):
    target = tmp_path / "extension/models" / resource
    target.parent.mkdir(parents=True)
    target.write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="Forbidden"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


def test_omitted_models_are_allowed_but_partial_bundles_are_rejected(tmp_path, monkeypatch):
    packaging.artifact_report(tmp_path, "cpu", require_runtime=False)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"files": {name: {
        "size": 5, "sha256": packaging.hashlib.sha256(b"model").hexdigest()
    } for name in ("one.onnx", "two.onnx")}}))
    monkeypatch.setattr(packaging, "MODEL_CONTRACT", contract)
    target = tmp_path / "extension/models/one.onnx"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model")
    with pytest.raises(ValueError, match="Incomplete bundled model set"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


@pytest.mark.parametrize("name", ["onnxruntime/datasets/sample.onnx", "unused/source.pt", "weights.safetensors"])
def test_models_outside_verified_staging_are_rejected(tmp_path, name):
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unnecessary model")
    with pytest.raises(ValueError, match="unexpected model resource"):
        packaging.artifact_report(tmp_path, "cpu", require_runtime=False)


@pytest.mark.parametrize("name", ["torch.nn", "torchvision.ops", "triton", "nvidia.cuda_runtime"])
def test_compilation_report_rejects_forbidden_modules(tmp_path, name):
    report = tmp_path / "report.xml"
    report.write_text(f'<nuitka-report><module name="{name}" /></nuitka-report>')
    with pytest.raises(ValueError, match="forbidden inference modules"):
        packaging.check_compilation_report(report)


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


@pytest.mark.parametrize(
    ("library_relative", "install_name"),
    [
        ("libonnxruntime.1.dylib", "@loader_path/../../libonnxruntime.1.dylib"),
        (
            "shared libraries/libonnxruntime.1.dylib",
            "@loader_path/../../shared libraries/libonnxruntime.1.dylib",
        ),
    ],
)
def test_macos_repair_resolves_collected_libraries_and_signs_after_rewrite(
    tmp_path, library_relative, install_name
):
    bundle = tmp_path / "entrypoint.app"
    binding = bundle / "Contents/MacOS/onnxruntime/capi/onnxruntime_pybind11_state.so"
    binding.parent.mkdir(parents=True)
    binding.write_bytes(b"Mach-O fixture")
    library = bundle / "Contents/MacOS" / library_relative
    library.parent.mkdir(parents=True, exist_ok=True)
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
            install_name,
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
