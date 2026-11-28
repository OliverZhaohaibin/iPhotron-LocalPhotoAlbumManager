"""Lazy, worker-owned ONNX sessions shared by People and Pets.

This module owns runtime policy, never models, library state, or task admission.
There is no process singleton: worker teardown releases every native session.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import NoReturn

_LOG = logging.getLogger(__name__)
CPU = "CPUExecutionProvider"
_PROVIDERS = {
    "win32": ("DmlExecutionProvider", "CUDAExecutionProvider"),
    # The CPU wheel also advertises CoreML. Availability alone is not a latency
    # qualification: DINOv2's partitioned CoreML path was slower than CPU in
    # source measurements. Keep it explicitly selectable through the shared
    # provider override rather than slowing the default lightweight profile.
    "darwin": (),
    "linux": ("CUDAExecutionProvider", "OpenVINOExecutionProvider"),
}
_MODEL_ERRORS = {
    "InvalidGraph",
    "InvalidProtobuf",
    "NoSuchFile",
    "InvalidArgument",
    "NotImplemented",
}


class RecognitionRuntimeUnavailableError(RuntimeError):
    """The worker's runtime cannot initialize or continue inference."""


class RecognitionProviderError(RecognitionRuntimeUnavailableError):
    """A device failed and its one CPU recovery could not complete."""


class RecognitionModelError(RuntimeError):
    """An unreadable or unsupported model must be repaired, not retried on a GPU."""


def _provider_failure(exc: Exception) -> bool:
    """Only recognized provider/device failures permit a CPU retry."""
    if type(exc).__name__ in _MODEL_ERRORS:
        return False
    detail = str(exc).casefold()
    return (
        type(exc).__name__ == "EPFail"
        or any(
            token in detail
            for token in (
                "cuda failure",
                "cuda error",
                "cudnn",
                "cublas",
                "cudaexecutionprovider",
                "failed to load library",
                "loadlibrary failed",
                "dll load failed",
                "coremlexecutionprovider",
                "coreml execution",
                "coreml provider",
                "dmlexecutionprovider",
                "dml execution",
                "dxgi_error_device",
                "device removed",
                "device lost",
                "device unavailable",
                "failed to create a working directory appropriate for url",  # CoreML compiler sandbox/cache
                "openvinoexecutionprovider",
                "openvino device",
                "failed to create execution provider",
            )
        )
    )


@dataclass(frozen=True)
class RecognitionRuntimePolicy:
    # Face and Pet workers can run concurrently, but each executes one model at
    # a time. At most two compute threads per worker leave the GUI CPU headroom.
    intra_op_threads: int

    @classmethod
    def from_environment(cls) -> RecognitionRuntimePolicy:
        cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 1
        budget = max(1, min(2, (cpus - 1) // 2))
        raw = os.environ.get("IPHOTO_RECOGNITION_THREADS", "").strip()
        if raw:
            try:
                budget = max(1, min(budget, int(raw)))
            except ValueError:
                _LOG.warning("Ignoring invalid IPHOTO_RECOGNITION_THREADS=%r", raw)
        return cls(budget)


def execution_providers(ort, requested: Sequence[str] | None = None) -> list[str]:
    available = set(ort.get_available_providers())
    configured = os.environ.get("IPHOTO_RECOGNITION_PROVIDERS", "").strip()
    if requested is None and configured:
        requested = [name.strip() for name in configured.split(",") if name.strip()]
    preferred = (
        requested if requested is not None else _PROVIDERS.get(sys.platform, _PROVIDERS["linux"])
    )
    selected = list(dict.fromkeys(name for name in preferred if name != CPU and name in available))
    if requested and any(name not in available for name in requested):
        _LOG.warning(
            "Recognition providers unavailable: requested=%s available=%s",
            requested,
            sorted(available),
        )
    # An advertised provider is only a candidate. Session construction below
    # must confirm it; CPU is always the final bounded recovery path.
    return [*selected, CPU]


class ManagedOnnxSession:
    """InferenceSession-compatible adapter with one explicit device recovery."""

    def __init__(self, ort, path: Path, policy: RecognitionRuntimePolicy, providers: list[str]):
        self._ort = ort
        self._path = path
        self._policy = policy
        self._lock = RLock()
        self._session = None
        self._failure: RecognitionRuntimeUnavailableError | RecognitionModelError | None = None
        self.requested_providers = tuple(providers)
        self.fallback_reason: str | None = None
        self._cpu_retried = providers == [CPU]
        try:
            self._session = self._create(providers)
        except Exception as exc:
            if type(exc).__name__ in _MODEL_ERRORS:
                raise RecognitionModelError(f"Invalid ONNX model {path}: {exc}") from exc
            if self._cpu_retried or not _provider_failure(exc):
                raise RecognitionRuntimeUnavailableError(
                    f"Cannot initialize ONNX model {path}: {exc}"
                ) from exc
            self._recover_cpu(exc)
        actual = self.get_providers()
        if providers != [CPU] and not any(name in actual for name in providers if name != CPU):
            # Native provider registration may decline an accelerator without
            # raising. Record that outcome even with Python fallback disabled.
            self.fallback_reason = (
                self.fallback_reason or "requested accelerator did not initialize"
            )
            self._cpu_retried = True
            _LOG.warning("Recognition CPU fallback: model=%s reason=%s", path, self.fallback_reason)
        _LOG.info(
            "Recognition ONNX session: model=%s requested=%s actual=%s threads=%d fallback=%s",
            path,
            providers,
            actual,
            policy.intra_op_threads,
            self.fallback_reason,
        )

    def _create(self, providers: list[str]):
        options = self._ort.SessionOptions()
        options.intra_op_num_threads = self._policy.intra_op_threads
        options.inter_op_num_threads = 1
        options.execution_mode = self._ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = self._ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_mem_pattern = "DmlExecutionProvider" not in providers
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        provider_options = []
        for provider in providers:
            if provider == "CUDAExecutionProvider":
                provider_options.append({"use_tf32": "0"})
            elif provider == "OpenVINOExecutionProvider":
                device = os.environ.get("IPHOTO_RECOGNITION_OPENVINO_DEVICE", "CPU")
                device_config = {"NUM_STREAMS": "1", "INFERENCE_PRECISION_HINT": "f32"}
                device_kind = "GPU" if device.startswith("GPU") else "CPU"
                if device_kind == "CPU":
                    device_config["INFERENCE_NUM_THREADS"] = str(self._policy.intra_op_threads)
                provider_options.append(
                    {
                        "device_type": device,
                        "load_config": json.dumps({device_kind: device_config}),
                    }
                )
            else:
                provider_options.append({})
        session = self._ort.InferenceSession(
            str(self._path),
            sess_options=options,
            providers=providers,
            provider_options=provider_options,
            enable_fallback=False,
        )
        # ORT's run() otherwise silently retries its own fallback list on EPFail.
        # Recovery and diagnostics must have one owner and one retry budget.
        session.disable_fallback()
        return session

    def _recover_cpu(self, exc: Exception) -> None:
        self._cpu_retried = True
        self.fallback_reason = f"{type(exc).__name__}: {exc}"
        _LOG.warning(
            "Recognition CPU fallback: model=%s reason=%s", self._path, self.fallback_reason
        )
        self._session = None  # release the failed device session before allocating another
        try:
            self._session = self._create([CPU])
        except Exception as cpu_exc:
            if type(cpu_exc).__name__ in _MODEL_ERRORS:
                self._fail(RecognitionModelError(
                    f"Invalid ONNX model {self._path}: {cpu_exc}"
                ), cpu_exc)
            self._fail(RecognitionProviderError(
                f"ONNX CPU fallback failed: {cpu_exc}"
            ), cpu_exc)

    def _fail(
        self, failure: RecognitionRuntimeUnavailableError | RecognitionModelError, cause: Exception
    ) -> NoReturn:
        # A terminal session must never turn later photos into asset failures.
        # A new worker owns recovery; this worker retains the typed root cause.
        self._failure = failure
        self._session = None
        raise failure from cause

    def _check_open(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._session is None:
            raise RecognitionRuntimeUnavailableError("ONNX session is closed")

    def run(self, output_names, input_feed, run_options=None):
        # DirectML permits a single Run at a time on each session. The lock also
        # makes close/recovery atomic with respect to native inference.
        with self._lock:
            self._check_open()
            try:
                return self._session.run(output_names, input_feed, run_options)
            except Exception as exc:
                if type(exc).__name__ in _MODEL_ERRORS:
                    self._fail(RecognitionModelError(f"ONNX model contract failed: {exc}"), exc)
                if _provider_failure(exc):
                    if self._cpu_retried:
                        self._fail(RecognitionProviderError(f"ONNX provider failed: {exc}"), exc)
                    self._recover_cpu(exc)
                    try:
                        return self._session.run(output_names, input_feed, run_options)
                    except Exception as cpu_exc:
                        if type(cpu_exc).__name__ in _MODEL_ERRORS:
                            self._fail(RecognitionModelError(
                                f"ONNX model contract failed: {cpu_exc}"
                            ), cpu_exc)
                        self._fail(RecognitionProviderError(
                            f"ONNX CPU fallback inference failed: {cpu_exc}"
                        ), cpu_exc)
                if isinstance(exc, (RuntimeError, OSError)) or type(exc).__name__ in {
                    "Fail", "RuntimeException", "EngineError"
                }:
                    self._fail(RecognitionRuntimeUnavailableError(
                        f"ONNX inference runtime failed: {exc}"
                    ), exc)
                raise

    def get_inputs(self):
        self._check_open()
        return self._session.get_inputs()

    def get_outputs(self):
        self._check_open()
        return self._session.get_outputs()

    def get_providers(self):
        self._check_open()
        return self._session.get_providers()

    def get_modelmeta(self):
        self._check_open()
        return self._session.get_modelmeta()

    def close(self) -> None:
        with self._lock:
            self._session = None
            self._failure = None


class RecognitionOnnxRuntime:
    def __init__(self, *, policy: RecognitionRuntimePolicy | None = None):
        self.policy = policy or RecognitionRuntimePolicy.from_environment()
        self._sessions: list[ManagedOnnxSession] = []
        self._dll_directories = []
        self._closed = False

    def ensure_available(self):
        """Check the optional runtime on demand before attempting model recovery."""
        if self._closed:
            raise RecognitionRuntimeUnavailableError("Recognition runtime is closed")
        try:
            if sys.platform == "win32" and not self._dll_directories:
                # Frozen ORT provider DLLs remain beside the pybind extension.
                # Keep AddDllDirectory handles alive for exactly this worker.
                from importlib.util import find_spec

                spec = find_spec("onnxruntime")
                if spec is not None and spec.origin:
                    capi = Path(spec.origin).parent / "capi"
                    if capi.is_dir():
                        self._dll_directories.append(os.add_dll_directory(str(capi)))
            ort = importlib.import_module("onnxruntime")
        except (ImportError, OSError) as exc:
            raise RecognitionRuntimeUnavailableError(
                "Install the optional ONNX recognition runtime"
            ) from exc
        return ort

    def create_session(
        self, path: Path, *, providers: Sequence[str] | None = None
    ) -> ManagedOnnxSession:
        ort = self.ensure_available()
        try:
            selected = execution_providers(ort, providers)
        except (OSError, RuntimeError) as exc:
            raise RecognitionRuntimeUnavailableError(f"Cannot query ONNX providers: {exc}") from exc
        session = ManagedOnnxSession(ort, Path(path), self.policy, selected)
        self._sessions.append(session)
        return session

    def close(self) -> None:
        self._closed = True
        for session in self._sessions:
            session.close()
        self._sessions.clear()
        for handle in self._dll_directories:
            handle.close()
        self._dll_directories.clear()
