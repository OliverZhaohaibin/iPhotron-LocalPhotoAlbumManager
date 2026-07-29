"""Windows Imaging Component still-image decoder for Detail surfaces."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any
import uuid

from PySide6.QtGui import QColorSpace, QImage

from iPhoto.gui.detail_decode_backend import (
    CancellationToken,
    DecodedSurface,
    _check_cancelled,
    _target_size,
)
from iPhoto.gui.detail_pipeline import DetailDecodeKey, DetailRenderRequest


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> _GUID:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


_CLSID_WIC_FACTORY = _GUID.from_string("cacaf262-9370-4615-a13b-9f5539da4c0a")
_IID_WIC_FACTORY = _GUID.from_string("ec5ec8a9-c395-4314-9c77-54d7a935ff70")
_PIXEL_FORMAT_32BPP_RGBA = _GUID.from_string("f5c7ad2d-6a8d-43dd-a7a8-a29935261ae9")

_CLSCTX_INPROC_SERVER = 0x1
_COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = -2147417850
_GENERIC_READ = 0x80000000
_WIC_DECODE_METADATA_ON_DEMAND = 0
_WIC_INTERPOLATION_FANT = 3
# HRESULT is a signed 32-bit value.  ``ctypes.wintypes`` deliberately does not
# define it on every supported CPython build, so use the ABI type directly.
_HRESULT = ctypes.c_int32


def _failed(result: int) -> bool:
    return _HRESULT(result).value < 0


def _check_hresult(result: int, operation: str) -> None:
    if _failed(result):
        code = ctypes.c_uint32(result).value
        raise OSError(code, f"{operation} failed with HRESULT 0x{code:08X}")


def _method(
    interface: ctypes.c_void_p,
    index: int,
    result_type: Any,
    *argument_types: Any,
) -> Any:
    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    prototype = ctypes.WINFUNCTYPE(
        result_type,
        ctypes.c_void_p,
        *argument_types,
    )
    return prototype(vtable[index])


def _release(interface: ctypes.c_void_p | None) -> None:
    if interface and interface.value:
        _method(interface, 2, wintypes.ULONG)(interface)


@dataclass(slots=True)
class _ComApartment:
    ole32: Any
    must_uninitialize: bool

    @classmethod
    def enter(cls) -> _ComApartment:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        ole32.CoInitializeEx.restype = _HRESULT
        result = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
        signed = _HRESULT(result).value
        if _failed(result) and signed != _RPC_E_CHANGED_MODE:
            _check_hresult(result, "CoInitializeEx")
        return cls(ole32=ole32, must_uninitialize=signed != _RPC_E_CHANGED_MODE)

    def close(self) -> None:
        if self.must_uninitialize:
            self.ole32.CoUninitialize()


def _create_factory(apartment: _ComApartment) -> ctypes.c_void_p:
    factory = ctypes.c_void_p()
    apartment.ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    apartment.ole32.CoCreateInstance.restype = _HRESULT
    result = apartment.ole32.CoCreateInstance(
        ctypes.byref(_CLSID_WIC_FACTORY),
        None,
        _CLSCTX_INPROC_SERVER,
        ctypes.byref(_IID_WIC_FACTORY),
        ctypes.byref(factory),
    )
    _check_hresult(result, "CoCreateInstance(IWICImagingFactory)")
    return factory


def _create_decoder(
    factory: ctypes.c_void_p,
    source: Path,
) -> ctypes.c_void_p:
    decoder = ctypes.c_void_p()
    create = _method(
        factory,
        3,
        _HRESULT,
        wintypes.LPCWSTR,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    result = create(
        factory,
        str(source),
        None,
        _GENERIC_READ,
        _WIC_DECODE_METADATA_ON_DEMAND,
        ctypes.byref(decoder),
    )
    _check_hresult(result, "IWICImagingFactory.CreateDecoderFromFilename")
    return decoder


def _first_frame(decoder: ctypes.c_void_p) -> ctypes.c_void_p:
    frame = ctypes.c_void_p()
    get_frame = _method(
        decoder,
        13,
        _HRESULT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(get_frame(decoder, 0, ctypes.byref(frame)), "IWICBitmapDecoder.GetFrame")
    return frame


def _source_size(source: ctypes.c_void_p) -> tuple[int, int]:
    width = wintypes.UINT()
    height = wintypes.UINT()
    get_size = _method(
        source,
        3,
        _HRESULT,
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT),
    )
    _check_hresult(get_size(source, ctypes.byref(width), ctypes.byref(height)), "IWICBitmapSource.GetSize")
    return int(width.value), int(height.value)


def _orientation_transform(orientation: int) -> int:
    # WICBitmapTransformOptions: Rotate90/180/270=1/2/3, FlipHorizontal=8.
    return {
        2: 8,
        3: 2,
        4: 10,
        5: 9,
        6: 1,
        7: 11,
        8: 3,
    }.get(orientation, 0)


def _apply_orientation(
    factory: ctypes.c_void_p,
    source: ctypes.c_void_p,
    orientation: int,
) -> ctypes.c_void_p:
    transform = _orientation_transform(orientation)
    if transform == 0:
        return ctypes.c_void_p()
    rotator = ctypes.c_void_p()
    create = _method(
        factory,
        13,
        _HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(create(factory, ctypes.byref(rotator)), "IWICImagingFactory.CreateBitmapFlipRotator")
    initialize = _method(
        rotator,
        8,
        _HRESULT,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    try:
        _check_hresult(initialize(rotator, source, transform), "IWICBitmapFlipRotator.Initialize")
    except Exception:
        _release(rotator)
        raise
    return rotator


def _scale_source(
    factory: ctypes.c_void_p,
    source: ctypes.c_void_p,
    width: int,
    height: int,
) -> ctypes.c_void_p:
    scaler = ctypes.c_void_p()
    create = _method(
        factory,
        11,
        _HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(create(factory, ctypes.byref(scaler)), "IWICImagingFactory.CreateBitmapScaler")
    initialize = _method(
        scaler,
        8,
        _HRESULT,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.DWORD,
    )
    try:
        _check_hresult(
            initialize(scaler, source, width, height, _WIC_INTERPOLATION_FANT),
            "IWICBitmapScaler.Initialize",
        )
    except Exception:
        _release(scaler)
        raise
    return scaler


def _convert_rgba(factory: ctypes.c_void_p, source: ctypes.c_void_p) -> ctypes.c_void_p:
    converter = ctypes.c_void_p()
    create = _method(
        factory,
        10,
        _HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(create(factory, ctypes.byref(converter)), "IWICImagingFactory.CreateFormatConverter")
    initialize = _method(
        converter,
        8,
        _HRESULT,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_double,
        wintypes.DWORD,
    )
    try:
        _check_hresult(
            initialize(
                converter,
                source,
                ctypes.byref(_PIXEL_FORMAT_32BPP_RGBA),
                0,
                None,
                0.0,
                0,
            ),
            "IWICFormatConverter.Initialize",
        )
    except Exception:
        _release(converter)
        raise
    return converter


def _create_color_context(factory: ctypes.c_void_p) -> ctypes.c_void_p:
    context = ctypes.c_void_p()
    create = _method(
        factory,
        15,
        _HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(
        create(factory, ctypes.byref(context)),
        "IWICImagingFactory.CreateColorContext",
    )
    return context


def _frame_color_context(
    factory: ctypes.c_void_p,
    frame: ctypes.c_void_p,
) -> ctypes.c_void_p:
    """Return the frame's first embedded color context, if it has one."""

    actual_count = wintypes.UINT()
    get_contexts = _method(
        frame,
        9,
        _HRESULT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    result = get_contexts(frame, 0, None, ctypes.byref(actual_count))
    if _failed(result) or actual_count.value == 0:
        return ctypes.c_void_p()

    context = _create_color_context(factory)
    contexts = (ctypes.c_void_p * 1)(context.value)
    try:
        _check_hresult(
            get_contexts(frame, 1, contexts, ctypes.byref(actual_count)),
            "IWICBitmapFrameDecode.GetColorContexts",
        )
        if actual_count.value == 0:
            _release(context)
            return ctypes.c_void_p()
    except Exception:
        _release(context)
        raise
    return context


def _create_srgb_color_context(factory: ctypes.c_void_p) -> ctypes.c_void_p:
    context = _create_color_context(factory)
    initialize = _method(
        context,
        5,
        _HRESULT,
        wintypes.UINT,
    )
    try:
        _check_hresult(
            initialize(context, 1),
            "IWICColorContext.InitializeFromExifColorSpace(sRGB)",
        )
    except Exception:
        _release(context)
        raise
    return context


def _transform_to_srgb(
    factory: ctypes.c_void_p,
    source: ctypes.c_void_p,
    source_context: ctypes.c_void_p,
    target_context: ctypes.c_void_p,
) -> ctypes.c_void_p:
    transform = ctypes.c_void_p()
    create = _method(
        factory,
        16,
        _HRESULT,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _check_hresult(
        create(factory, ctypes.byref(transform)),
        "IWICImagingFactory.CreateColorTransformer",
    )
    initialize = _method(
        transform,
        8,
        _HRESULT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
    )
    try:
        _check_hresult(
            initialize(
                transform,
                source,
                source_context,
                target_context,
                ctypes.byref(_PIXEL_FORMAT_32BPP_RGBA),
            ),
            "IWICColorTransform.Initialize",
        )
    except Exception:
        _release(transform)
        raise
    return transform


def _copy_rgba(source: ctypes.c_void_p, width: int, height: int) -> QImage:
    stride = width * 4
    size = stride * height
    pixels = (ctypes.c_ubyte * size)()
    copy_pixels = _method(
        source,
        7,
        _HRESULT,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_ubyte),
    )
    _check_hresult(
        copy_pixels(source, None, stride, size, pixels),
        "IWICBitmapSource.CopyPixels",
    )
    image = QImage(bytes(pixels), width, height, stride, QImage.Format.Format_RGBA8888).copy()
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return image


class WindowsWicStillDecodeBackend:
    """Decode an indexed still through WIC and return detached RGBA8888/sRGB."""

    name = "wic"

    def decode(
        self,
        request: DetailRenderRequest,
        cancellation: CancellationToken,
    ) -> DecodedSurface:
        prepared = request.with_decode_level()
        target = _target_size(prepared)
        _check_cancelled(cancellation)
        apartment = _ComApartment.enter()
        factory = decoder = frame = oriented = scaler = converter = None
        source_color = target_color = color_transform = None
        try:
            factory = _create_factory(apartment)
            decoder = _create_decoder(factory, prepared.source_identity.path)
            frame = _first_frame(decoder)
            intrinsic = _source_size(frame)
            oriented = _apply_orientation(
                factory,
                frame,
                prepared.source_identity.orientation,
            )
            current = oriented if oriented and oriented.value else frame
            current_size = _source_size(current)
            scaled_width, scaled_height = current_size
            if current_size[0] > target.width() or current_size[1] > target.height():
                ratio = min(
                    target.width() / current_size[0],
                    target.height() / current_size[1],
                )
                scaled_width = max(1, round(current_size[0] * ratio))
                scaled_height = max(1, round(current_size[1] * ratio))
                scaler = _scale_source(factory, current, scaled_width, scaled_height)
                current = scaler
            _check_cancelled(cancellation)
            source_color = _frame_color_context(factory, frame)
            if source_color and source_color.value:
                target_color = _create_srgb_color_context(factory)
                color_transform = _transform_to_srgb(
                    factory,
                    current,
                    source_color,
                    target_color,
                )
                rgba_source = color_transform
            else:
                converter = _convert_rgba(factory, current)
                rgba_source = converter
            image = _copy_rgba(rgba_source, scaled_width, scaled_height)
            _check_cancelled(cancellation)
        finally:
            for interface in (
                converter,
                color_transform,
                target_color,
                source_color,
                scaler,
                oriented,
                frame,
                decoder,
                factory,
            ):
                _release(interface)
            apartment.close()
        if image.isNull():
            raise RuntimeError("WIC returned an empty neutral surface")
        return DecodedSurface(
            image=image,
            decode_key=DetailDecodeKey.from_request(prepared),
            source_size=(
                max(1, prepared.source_identity.width or intrinsic[0]),
                max(1, prepared.source_identity.height or intrinsic[1]),
            ),
            decoded_size=(image.width(), image.height()),
            decode_level=prepared.decode_level or "full",
            backend=self.name,
        )


def create_windows_wic_backend() -> WindowsWicStillDecodeBackend | None:
    """Return the production WIC backend only on Windows."""

    if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
        return None
    return WindowsWicStillDecodeBackend()


__all__ = ["WindowsWicStillDecodeBackend", "create_windows_wic_backend"]

