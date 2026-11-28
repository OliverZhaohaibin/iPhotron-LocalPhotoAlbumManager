from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iPhoto.recognition import onnx_runtime as runtime


class FakeSession:
    def __init__(self, providers, failure=None):
        self.providers = providers
        self.failure = failure
        self.disabled = False

    def disable_fallback(self):
        self.disabled = True

    def get_providers(self):
        return self.providers

    def run(self, *_):
        if self.failure:
            raise self.failure
        return ["result"]


class Options:
    def __init__(self):
        self.config = {}

    def add_session_config_entry(self, key, value):
        self.config[key] = value


def fake_ort(monkeypatch, *, available, construct=None):
    sessions, options, attempts = [], [], []

    def create(_path, *, sess_options, providers, provider_options, enable_fallback):
        assert enable_fallback is False
        attempts.append(providers)
        sess_options.provider_options = provider_options
        options.append(sess_options)
        session = construct(providers) if construct else FakeSession(providers)
        sessions.append(session)
        return session

    ort = SimpleNamespace(
        get_available_providers=lambda: available,
        InferenceSession=create,
        SessionOptions=Options,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL="all"),
    )
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: ort)
    monkeypatch.delenv("IPHOTO_RECOGNITION_PROVIDERS", raising=False)
    return sessions, options, attempts


@pytest.mark.parametrize(
    ("platform", "provider"),
    [
        ("win32", "DmlExecutionProvider"),
        ("win32", "CUDAExecutionProvider"),
        ("darwin", "CoreMLExecutionProvider"),
        ("linux", "CUDAExecutionProvider"),
        ("linux", "OpenVINOExecutionProvider"),
    ],
)
def test_platform_candidates_use_actual_sessions_and_bounded_threads(
    monkeypatch, platform, provider
):
    monkeypatch.setattr(runtime.sys, "platform", platform)
    # Avoid testing the host's Win32 AddDllDirectory API through a fake platform.
    monkeypatch.setattr(
        runtime.os,
        "add_dll_directory",
        lambda _: SimpleNamespace(close=lambda: None),
        raising=False,
    )
    sessions, options, attempts = fake_ort(monkeypatch, available=[provider, runtime.CPU])
    owner = runtime.RecognitionOnnxRuntime()
    session = owner.create_session(Path("model.onnx"), providers=[provider])
    assert attempts == [[provider, runtime.CPU]]
    assert session.get_providers() == [provider, runtime.CPU]
    assert sessions[0].disabled
    assert 1 <= options[0].intra_op_num_threads <= 2
    assert options[0].inter_op_num_threads == 1
    assert options[0].execution_mode == "sequential"
    assert options[0].enable_mem_pattern is (provider != "DmlExecutionProvider")
    assert set(options[0].config.values()) == {"0"}
    if provider == "CUDAExecutionProvider":
        assert options[0].provider_options[0] == {"use_tf32": "0"}
    if provider == "OpenVINOExecutionProvider":
        import json

        config = json.loads(options[0].provider_options[0]["load_config"])
        assert config["CPU"]["INFERENCE_NUM_THREADS"] == str(owner.policy.intra_op_threads)
        assert config["CPU"]["INFERENCE_PRECISION_HINT"] == "f32"
    owner.close()
    assert session._session is None


def test_macos_cpu_wheel_does_not_implicitly_enable_unqualified_coreml(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    _, _, attempts = fake_ort(monkeypatch, available=["CoreMLExecutionProvider", runtime.CPU])
    runtime.RecognitionOnnxRuntime().create_session(Path("model.onnx"))
    assert attempts == [[runtime.CPU]]
    monkeypatch.setenv("IPHOTO_RECOGNITION_PROVIDERS", "CoreMLExecutionProvider,CPUExecutionProvider")
    runtime.RecognitionOnnxRuntime().create_session(Path("model.onnx"))
    assert attempts[-1] == ["CoreMLExecutionProvider", runtime.CPU]


def test_unavailable_gpu_is_never_passed_to_ort(monkeypatch):
    _, _, attempts = fake_ort(monkeypatch, available=[runtime.CPU])
    owner = runtime.RecognitionOnnxRuntime()
    session = owner.create_session(Path("model.onnx"), providers=["CUDAExecutionProvider"])
    assert attempts == [[runtime.CPU]]
    assert session.run(None, {}) == ["result"]


@pytest.mark.parametrize(
    "detail", ["LoadLibrary failed for provider", "CUDA failure 999", "DXGI_ERROR_DEVICE_REMOVED"]
)
def test_gpu_initialization_failure_retries_cpu_once(monkeypatch, detail):
    def construct(providers):
        if providers != [runtime.CPU]:
            raise RuntimeError(detail)
        return FakeSession(providers)

    _, _, attempts = fake_ort(
        monkeypatch, available=["CUDAExecutionProvider", runtime.CPU], construct=construct
    )
    session = runtime.RecognitionOnnxRuntime().create_session(
        Path("model.onnx"), providers=["CUDAExecutionProvider"]
    )
    assert attempts == [["CUDAExecutionProvider", runtime.CPU], [runtime.CPU]]
    assert detail in session.fallback_reason


def test_advertised_gpu_that_ort_did_not_activate_is_recorded(monkeypatch):
    fake_ort(
        monkeypatch,
        available=["CUDAExecutionProvider", runtime.CPU],
        construct=lambda _: FakeSession([runtime.CPU]),
    )
    session = runtime.RecognitionOnnxRuntime().create_session(
        Path("model.onnx"), providers=["CUDAExecutionProvider"]
    )
    assert session.get_providers() == [runtime.CPU]
    assert "did not initialize" in session.fallback_reason


def test_device_failure_during_inference_has_one_cpu_retry(monkeypatch):
    def construct(providers):
        return FakeSession(providers, RuntimeError("CUDA failure 999"))

    _, _, attempts = fake_ort(
        monkeypatch, available=["CUDAExecutionProvider", runtime.CPU], construct=construct
    )
    session = runtime.RecognitionOnnxRuntime().create_session(
        Path("model.onnx"), providers=["CUDAExecutionProvider"]
    )
    with pytest.raises(RuntimeError, match="CUDA failure"):
        session.run(None, {})
    with pytest.raises(RuntimeError, match="CUDA failure"):
        session.run(None, {})
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "error_name", ["InvalidGraph", "InvalidProtobuf", "InvalidArgument", "NotImplemented"]
)
def test_model_errors_do_not_trigger_provider_fallback(monkeypatch, error_name):
    error = type(error_name, (Exception,), {})("model contract broken")

    def construct(_):
        raise error

    _, _, attempts = fake_ort(
        monkeypatch, available=[runtime.CPU, "CUDAExecutionProvider"], construct=construct
    )
    with pytest.raises(runtime.RecognitionModelError):
        runtime.RecognitionOnnxRuntime().create_session(
            Path("bad.onnx"), providers=["CUDAExecutionProvider"]
        )
    assert len(attempts) == 1


def test_arbitrary_inference_error_does_not_retry_or_change_providers(monkeypatch):
    _, _, attempts = fake_ort(
        monkeypatch,
        available=["CUDAExecutionProvider", runtime.CPU],
        construct=lambda providers: FakeSession(providers, ValueError("bad input shape")),
    )
    session = runtime.RecognitionOnnxRuntime().create_session(
        Path("model.onnx"), providers=["CUDAExecutionProvider"]
    )
    with pytest.raises(ValueError, match="bad input"):
        session.run(None, {})
    assert len(attempts) == 1


def test_runtime_missing_is_typed_and_initialization_is_lazy(monkeypatch):
    calls = []

    def missing(name):
        calls.append(name)
        raise ImportError("missing native runtime")

    monkeypatch.setattr(runtime.importlib, "import_module", missing)
    owner = runtime.RecognitionOnnxRuntime()
    assert calls == []
    with pytest.raises(runtime.RecognitionRuntimeUnavailableError):
        owner.create_session(Path("model.onnx"))


def test_library_worker_lifetimes_have_independent_sessions(monkeypatch):
    fake_ort(monkeypatch, available=[runtime.CPU])
    old, current = runtime.RecognitionOnnxRuntime(), runtime.RecognitionOnnxRuntime()
    a = old.create_session(Path("old.onnx"))
    b = current.create_session(Path("current.onnx"))
    old.close()
    old.close()
    with pytest.raises(runtime.RecognitionRuntimeUnavailableError, match="closed"):
        a.run(None, {})
    with pytest.raises(runtime.RecognitionRuntimeUnavailableError, match="closed"):
        old.create_session(Path("late.onnx"))
    assert b.run(None, {}) == ["result"]
    current.close()


def test_thread_override_cannot_expand_concurrent_worker_budget(monkeypatch):
    monkeypatch.setenv("IPHOTO_RECOGNITION_THREADS", "1000")
    assert runtime.RecognitionRuntimePolicy.from_environment().intra_op_threads <= 2
    monkeypatch.setenv("IPHOTO_RECOGNITION_THREADS", "1")
    assert runtime.RecognitionRuntimePolicy.from_environment().intra_op_threads == 1
