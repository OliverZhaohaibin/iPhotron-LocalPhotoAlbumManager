"""AOT-loaded native helpers for scan hot paths.

The native library is optional. Python first attempts to load the
platform-specific binary from this package directory and transparently
falls back to pure-Python implementations when the binary is missing or
cannot be loaded.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

INT64_MIN = -(2**63)

FEATURE_P1 = "p1_iso8601_datetime"
FEATURE_P2 = "p2_file_hash"
FEATURE_P3 = "p3_glob_filter"
FEATURE_P4 = "p4_discovery"
FEATURE_P5 = "p5_metadata_timestamp"
FEATURE_P6 = "p6_content_id"

_ALL_FEATURES = (
    FEATURE_P1,
    FEATURE_P2,
    FEATURE_P3,
    FEATURE_P4,
    FEATURE_P5,
    FEATURE_P6,
)

RUNTIME_MODE_C_EXTENSION = "c_extension"
RUNTIME_MODE_PYTHON_FALLBACK = "python_fallback"

MEDIA_HINT_UNKNOWN = -1
MEDIA_HINT_IMAGE = 0
MEDIA_HINT_VIDEO = 1

DISCOVERY_CHUNK_ITEMS = 512
DISCOVERY_CHUNK_BYTES = 2 * 1024 * 1024
PREPARE_CHUNK_ITEMS = 128
PAIR_FEED_CHUNK_ITEMS = 1024
PAIR_RESULT_CHUNK_ITEMS = 512

_DISCOVERY_CALLBACK = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_void_p,
)


class _DiscoveryChunkItem(ctypes.Structure):
    _fields_ = [
        ("abs_path", ctypes.c_char_p),
        ("rel_path", ctypes.c_char_p),
        ("media_kind", ctypes.c_int),
    ]


class _PrepareScanInput(ctypes.Structure):
    _fields_ = [
        ("abs_path", ctypes.c_char_p),
        ("rel_path", ctypes.c_char_p),
        ("size_bytes", ctypes.c_int64),
        ("mtime_us", ctypes.c_int64),
        ("dt_value", ctypes.c_char_p),
        ("media_hint", ctypes.c_int),
    ]


class _PrepareScanOutput(ctypes.Structure):
    _fields_ = [
        ("ok", ctypes.c_int),
        ("file_id", ctypes.c_char * 33),
        ("ts", ctypes.c_int64),
        ("year", ctypes.c_int),
        ("month", ctypes.c_int),
        ("media_type", ctypes.c_int),
    ]


class _PairRowInput(ctypes.Structure):
    _fields_ = [
        ("rel", ctypes.c_char_p),
        ("mime", ctypes.c_char_p),
        ("dt", ctypes.c_char_p),
        ("content_id", ctypes.c_char_p),
        ("dur", ctypes.c_double),
        ("still_image_time", ctypes.c_double),
        ("has_dur", ctypes.c_int),
        ("has_still_image_time", ctypes.c_int),
    ]


class _PairMatchOutput(ctypes.Structure):
    _fields_ = [
        ("still_index", ctypes.c_uint32),
        ("motion_index", ctypes.c_uint32),
        ("confidence", ctypes.c_double),
    ]


@dataclass(frozen=True)
class NativeStatus:
    runtime_mode: str
    available_features: tuple[str, ...]
    failure_reason: str | None = None


@dataclass(frozen=True)
class NativeDiscoveryItem:
    abs_path: str
    rel_path: str
    media_kind: int


@dataclass(frozen=True)
class NativePrepareScanInput:
    abs_path: str
    rel_path: str
    size_bytes: int
    mtime_us: int
    dt_value: str | None
    media_hint: int = MEDIA_HINT_UNKNOWN


@dataclass(frozen=True)
class NativePrepareScanResult:
    ok: bool
    file_id: str | None
    ts: int | None
    year: int | None
    month: int | None
    media_type: int | None


@dataclass(frozen=True)
class NativePairRowInput:
    rel: str | None
    mime: str | None
    dt: str | None
    content_id: str | None
    dur: float | None
    still_image_time: float | None


@dataclass(frozen=True)
class NativePairMatch:
    still_index: int
    motion_index: int
    confidence: float


@dataclass(frozen=True)
class NativePairExecution:
    matches: tuple[NativePairMatch, ...]
    feed_chunks: int
    result_chunks: int


_initialized = False
_lib: ctypes.CDLL | None = None
_status = NativeStatus(
    runtime_mode=RUNTIME_MODE_PYTHON_FALLBACK,
    available_features=(),
    failure_reason="native loader not initialised",
)


def _platform_library_name() -> str:
    if sys.platform == "win32":
        return "_scan_utils.dll"
    if sys.platform == "darwin":
        return "_scan_utils.dylib"
    return "_scan_utils.so"


def _library_path() -> Path:
    return Path(__file__).resolve().parent / _platform_library_name()


def _bind_library(lib: ctypes.CDLL) -> None:
    lib.parse_iso8601_to_unix_us.argtypes = [ctypes.c_char_p]
    lib.parse_iso8601_to_unix_us.restype = ctypes.c_int64

    lib.parse_iso8601_full.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.parse_iso8601_full.restype = ctypes.c_int

    lib.normalise_content_id.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
    ]
    lib.normalise_content_id.restype = ctypes.c_int

    lib.compute_file_id_c.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
    ]
    lib.compute_file_id_c.restype = ctypes.c_int

    lib.should_include_c.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
    ]
    lib.should_include_c.restype = ctypes.c_int

    lib.discover_files_c.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.c_int,
        _DISCOVERY_CALLBACK,
        ctypes.c_void_p,
    ]
    lib.discover_files_c.restype = ctypes.c_int

    lib.discovery_open_c.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_size_t,
        ctypes.c_int,
    ]
    lib.discovery_open_c.restype = ctypes.c_void_p

    lib.discovery_next_chunk_c.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(_DiscoveryChunkItem)),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.discovery_next_chunk_c.restype = ctypes.c_int

    lib.discovery_close_c.argtypes = [ctypes.c_void_p]
    lib.discovery_close_c.restype = None

    lib.prepare_scan_chunk_c.argtypes = [
        ctypes.POINTER(_PrepareScanInput),
        ctypes.c_size_t,
        ctypes.POINTER(_PrepareScanOutput),
    ]
    lib.prepare_scan_chunk_c.restype = ctypes.c_int

    lib.pair_ctx_create_c.argtypes = []
    lib.pair_ctx_create_c.restype = ctypes.c_void_p

    lib.pair_ctx_feed_rows_c.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_PairRowInput),
        ctypes.c_size_t,
    ]
    lib.pair_ctx_feed_rows_c.restype = ctypes.c_int

    lib.pair_ctx_finalize_next_chunk_c.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(_PairMatchOutput)),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.pair_ctx_finalize_next_chunk_c.restype = ctypes.c_int

    lib.pair_ctx_destroy_c.argtypes = [ctypes.c_void_p]
    lib.pair_ctx_destroy_c.restype = None


def _ensure_loaded() -> None:
    global _initialized, _lib, _status
    if _initialized:
        return

    _initialized = True
    lib_path = _library_path()
    if not lib_path.is_file():
        _status = NativeStatus(
            runtime_mode=RUNTIME_MODE_PYTHON_FALLBACK,
            available_features=(),
            failure_reason=f"native library missing: {lib_path.name}",
        )
        return

    try:
        candidate = ctypes.CDLL(str(lib_path))
    except OSError as exc:
        _status = NativeStatus(
            runtime_mode=RUNTIME_MODE_PYTHON_FALLBACK,
            available_features=(),
            failure_reason=f"native library load failed: {exc}",
        )
        return

    try:
        _bind_library(candidate)
    except AttributeError as exc:
        _status = NativeStatus(
            runtime_mode=RUNTIME_MODE_PYTHON_FALLBACK,
            available_features=(),
            failure_reason=f"native symbol binding failed: {exc}",
        )
        return

    _lib = candidate
    _status = NativeStatus(
        runtime_mode=RUNTIME_MODE_C_EXTENSION,
        available_features=_ALL_FEATURES,
        failure_reason=None,
    )


def _build_c_string_array(values: Sequence[str]) -> tuple[Sequence[bytes], ctypes.Array | None]:
    encoded = tuple(value.encode("utf-8") for value in values)
    if not encoded:
        return encoded, None
    array = (ctypes.c_char_p * len(encoded))(*encoded)
    return encoded, array


def _encode_path(value: os.PathLike[str] | str) -> bytes:
    return os.fspath(value).encode("utf-8")


def _encode_nullable(value: str | None) -> bytes | None:
    if value is None:
        return None
    return value.encode("utf-8")


def _pattern_requires_python(pattern: str) -> bool:
    return "[" in pattern or "]" in pattern


def _patterns_supported(patterns: Sequence[str]) -> bool:
    return not any(_pattern_requires_python(pattern) for pattern in patterns)


def get_runtime_status() -> NativeStatus:
    _ensure_loaded()
    return _status


def runtime_mode() -> str:
    return get_runtime_status().runtime_mode


def runtime_mode_label() -> str:
    if runtime_mode() == RUNTIME_MODE_C_EXTENSION:
        return "C extension"
    return "Python fallback"


def parse_iso8601_to_unix_us(value: str | None) -> int | None:
    if not value:
        return None
    _ensure_loaded()
    if _lib is None:
        return None
    result = int(_lib.parse_iso8601_to_unix_us(value.encode("utf-8")))
    return None if result == INT64_MIN else result


def parse_iso8601_full(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    _ensure_loaded()
    if _lib is None:
        return None

    unix_us = ctypes.c_int64()
    year = ctypes.c_int()
    month = ctypes.c_int()
    ok = int(
        _lib.parse_iso8601_full(
            value.encode("utf-8"),
            ctypes.byref(unix_us),
            ctypes.byref(year),
            ctypes.byref(month),
        )
    )
    if ok != 1:
        return None
    return int(unix_us.value), int(year.value), int(month.value)


def normalise_content_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    _ensure_loaded()
    if _lib is None:
        return None

    encoded = value.encode("utf-8")
    out = ctypes.create_string_buffer(len(encoded) + 1)
    written = int(_lib.normalise_content_id(encoded, out, len(out)))
    if written < 0:
        return None
    if written == 0:
        return None
    return out.value.decode("utf-8")


def compute_file_id(path: os.PathLike[str] | str) -> str | None:
    _ensure_loaded()
    if _lib is None:
        return None

    out = ctypes.create_string_buffer(33)
    ok = int(_lib.compute_file_id_c(_encode_path(path), out, len(out)))
    if ok != 1:
        return None
    return out.value.decode("ascii")


def should_include_rel(
    rel_path: str,
    include_globs: Sequence[str],
    exclude_globs: Sequence[str],
) -> bool | None:
    if not _patterns_supported(include_globs) or not _patterns_supported(exclude_globs):
        return None
    _ensure_loaded()
    if _lib is None:
        return None

    include_bytes, include_array = _build_c_string_array(include_globs)
    exclude_bytes, exclude_array = _build_c_string_array(exclude_globs)
    _ = include_bytes, exclude_bytes
    result = int(
        _lib.should_include_c(
            rel_path.encode("utf-8"),
            include_array,
            len(include_globs),
            exclude_array,
            len(exclude_globs),
        )
    )
    return bool(result)


def discover_files(
    root: os.PathLike[str] | str,
    *,
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    supported_extensions: Sequence[str] = (),
    skip_dir_names: Sequence[str] = (),
    skip_hidden_dirs: bool = False,
    on_found: Callable[[Path, str], bool | None],
) -> int | None:
    if not _patterns_supported(include_globs) or not _patterns_supported(exclude_globs):
        return None
    _ensure_loaded()
    if _lib is None:
        return None

    root_path = Path(root)
    callback_error: Exception | None = None

    def _on_found(path_ptr: bytes, rel_ptr: bytes, _userdata: object) -> int:
        nonlocal callback_error
        if callback_error is not None:
            return 1
        try:
            should_stop = on_found(
                Path(path_ptr.decode("utf-8")),
                rel_ptr.decode("utf-8"),
            )
        except Exception as exc:  # pragma: no cover - defensive bridge
            callback_error = exc
            return 1
        return 1 if should_stop else 0

    include_bytes, include_array = _build_c_string_array(include_globs)
    exclude_bytes, exclude_array = _build_c_string_array(exclude_globs)
    support_bytes, support_array = _build_c_string_array(
        tuple(ext.lower() for ext in supported_extensions)
    )
    skip_bytes, skip_array = _build_c_string_array(skip_dir_names)
    _ = include_bytes, exclude_bytes, support_bytes, skip_bytes

    callback = _DISCOVERY_CALLBACK(_on_found)
    count = int(
        _lib.discover_files_c(
            _encode_path(root_path),
            include_array,
            len(include_globs),
            exclude_array,
            len(exclude_globs),
            support_array,
            len(supported_extensions),
            skip_array,
            len(skip_dir_names),
            1 if skip_hidden_dirs else 0,
            callback,
            None,
        )
    )
    if callback_error is not None:
        raise callback_error
    return count


def iter_discovery_chunks(
    root: os.PathLike[str] | str,
    *,
    include_globs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    supported_extensions: Sequence[str] = (),
    skip_dir_names: Sequence[str] = (),
    skip_hidden_dirs: bool = False,
    max_items: int = DISCOVERY_CHUNK_ITEMS,
    max_bytes: int = DISCOVERY_CHUNK_BYTES,
) -> Iterator[tuple[NativeDiscoveryItem, ...]] | None:
    if not _patterns_supported(include_globs) or not _patterns_supported(exclude_globs):
        return None
    _ensure_loaded()
    if _lib is None:
        return None

    include_bytes, include_array = _build_c_string_array(include_globs)
    exclude_bytes, exclude_array = _build_c_string_array(exclude_globs)
    support_bytes, support_array = _build_c_string_array(
        tuple(ext.lower() for ext in supported_extensions)
    )
    skip_bytes, skip_array = _build_c_string_array(skip_dir_names)
    _ = include_bytes, exclude_bytes, support_bytes, skip_bytes

    handle = _lib.discovery_open_c(
        _encode_path(root),
        include_array,
        len(include_globs),
        exclude_array,
        len(exclude_globs),
        support_array,
        len(supported_extensions),
        skip_array,
        len(skip_dir_names),
        1 if skip_hidden_dirs else 0,
    )
    if not handle:
        return None

    def _iter() -> Iterator[tuple[NativeDiscoveryItem, ...]]:
        try:
            while True:
                items_ptr = ctypes.POINTER(_DiscoveryChunkItem)()
                count = ctypes.c_size_t()
                done = ctypes.c_int()
                ok = int(
                    _lib.discovery_next_chunk_c(
                        handle,
                        max_items,
                        max_bytes,
                        ctypes.byref(items_ptr),
                        ctypes.byref(count),
                        ctypes.byref(done),
                    )
                )
                if ok != 1:
                    return

                chunk = tuple(
                    NativeDiscoveryItem(
                        abs_path=items_ptr[index].abs_path.decode("utf-8"),
                        rel_path=items_ptr[index].rel_path.decode("utf-8"),
                        media_kind=int(items_ptr[index].media_kind),
                    )
                    for index in range(count.value)
                )
                if chunk:
                    yield chunk
                if done.value == 1:
                    break
        finally:
            _lib.discovery_close_c(handle)

    return _iter()


def prepare_scan_chunk(
    inputs: Sequence[NativePrepareScanInput],
) -> tuple[NativePrepareScanResult, ...] | None:
    _ensure_loaded()
    if _lib is None:
        return None
    if not inputs:
        return ()

    encoded_inputs = [
        (
            _encode_nullable(item.abs_path),
            _encode_nullable(item.rel_path),
            _encode_nullable(item.dt_value),
        )
        for item in inputs
    ]

    c_inputs = (_PrepareScanInput * len(inputs))()
    for index, item in enumerate(inputs):
        abs_bytes, rel_bytes, dt_bytes = encoded_inputs[index]
        c_inputs[index].abs_path = abs_bytes
        c_inputs[index].rel_path = rel_bytes
        c_inputs[index].size_bytes = int(item.size_bytes)
        c_inputs[index].mtime_us = int(item.mtime_us)
        c_inputs[index].dt_value = dt_bytes
        c_inputs[index].media_hint = int(item.media_hint)

    c_outputs = (_PrepareScanOutput * len(inputs))()
    ok = int(_lib.prepare_scan_chunk_c(c_inputs, len(inputs), c_outputs))
    if ok != 1:
        return None

    return tuple(
        NativePrepareScanResult(
            ok=bool(c_outputs[index].ok),
            file_id=(
                bytes(c_outputs[index].file_id).split(b"\0", 1)[0].decode("ascii")
                if c_outputs[index].ok
                else None
            ),
            ts=int(c_outputs[index].ts) if c_outputs[index].ok else None,
            year=int(c_outputs[index].year) if c_outputs[index].ok else None,
            month=int(c_outputs[index].month) if c_outputs[index].ok else None,
            media_type=int(c_outputs[index].media_type) if c_outputs[index].ok else None,
        )
        for index in range(len(inputs))
    )


def pair_rows(
    rows: Sequence[NativePairRowInput],
    *,
    feed_chunk_items: int = PAIR_FEED_CHUNK_ITEMS,
    result_chunk_items: int = PAIR_RESULT_CHUNK_ITEMS,
) -> NativePairExecution | None:
    _ensure_loaded()
    if _lib is None:
        return None
    if not rows:
        return NativePairExecution(matches=(), feed_chunks=0, result_chunks=0)

    handle = _lib.pair_ctx_create_c()
    if not handle:
        return None

    feed_chunks = 0
    result_chunks = 0
    matches: list[NativePairMatch] = []

    try:
        for start in range(0, len(rows), feed_chunk_items):
            chunk = rows[start : start + feed_chunk_items]
            encoded_chunk = [
                (
                    _encode_nullable(item.rel),
                    _encode_nullable(item.mime),
                    _encode_nullable(item.dt),
                    _encode_nullable(item.content_id),
                )
                for item in chunk
            ]
            c_chunk = (_PairRowInput * len(chunk))()
            for index, item in enumerate(chunk):
                rel_bytes, mime_bytes, dt_bytes, content_id_bytes = encoded_chunk[index]
                c_chunk[index].rel = rel_bytes
                c_chunk[index].mime = mime_bytes
                c_chunk[index].dt = dt_bytes
                c_chunk[index].content_id = content_id_bytes
                c_chunk[index].dur = float(item.dur or 0.0)
                c_chunk[index].still_image_time = float(item.still_image_time or 0.0)
                c_chunk[index].has_dur = 1 if item.dur is not None else 0
                c_chunk[index].has_still_image_time = (
                    1 if item.still_image_time is not None else 0
                )

            ok = int(_lib.pair_ctx_feed_rows_c(handle, c_chunk, len(chunk)))
            if ok != 1:
                return None
            feed_chunks += 1

        while True:
            chunk_ptr = ctypes.POINTER(_PairMatchOutput)()
            count = ctypes.c_size_t()
            done = ctypes.c_int()
            ok = int(
                _lib.pair_ctx_finalize_next_chunk_c(
                    handle,
                    result_chunk_items,
                    ctypes.byref(chunk_ptr),
                    ctypes.byref(count),
                    ctypes.byref(done),
                )
            )
            if ok != 1:
                return None

            if count.value:
                result_chunks += 1
                for index in range(count.value):
                    matches.append(
                        NativePairMatch(
                            still_index=int(chunk_ptr[index].still_index),
                            motion_index=int(chunk_ptr[index].motion_index),
                            confidence=float(chunk_ptr[index].confidence),
                        )
                    )

            if done.value == 1:
                break
    finally:
        _lib.pair_ctx_destroy_c(handle)

    return NativePairExecution(
        matches=tuple(matches),
        feed_chunks=feed_chunks,
        result_chunks=result_chunks,
    )


def _reset_state_for_tests() -> None:
    global _initialized, _lib, _status
    _initialized = False
    _lib = None
    _status = NativeStatus(
        runtime_mode=RUNTIME_MODE_PYTHON_FALLBACK,
        available_features=(),
        failure_reason="native loader not initialised",
    )


__all__ = [
    "DISCOVERY_CHUNK_BYTES",
    "DISCOVERY_CHUNK_ITEMS",
    "FEATURE_P1",
    "FEATURE_P2",
    "FEATURE_P3",
    "FEATURE_P4",
    "FEATURE_P5",
    "FEATURE_P6",
    "INT64_MIN",
    "MEDIA_HINT_IMAGE",
    "MEDIA_HINT_UNKNOWN",
    "MEDIA_HINT_VIDEO",
    "NativeDiscoveryItem",
    "NativePairExecution",
    "NativePairMatch",
    "NativePairRowInput",
    "NativePrepareScanInput",
    "NativePrepareScanResult",
    "NativeStatus",
    "PAIR_FEED_CHUNK_ITEMS",
    "PAIR_RESULT_CHUNK_ITEMS",
    "PREPARE_CHUNK_ITEMS",
    "RUNTIME_MODE_C_EXTENSION",
    "RUNTIME_MODE_PYTHON_FALLBACK",
    "compute_file_id",
    "discover_files",
    "get_runtime_status",
    "iter_discovery_chunks",
    "normalise_content_id",
    "pair_rows",
    "parse_iso8601_full",
    "parse_iso8601_to_unix_us",
    "prepare_scan_chunk",
    "runtime_mode",
    "runtime_mode_label",
    "should_include_rel",
]
