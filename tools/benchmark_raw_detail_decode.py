#!/usr/bin/env python3
"""Non-gating microbenchmark for the RAW Detail decode stages."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from PySide6.QtCore import QSize

from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.gui.detail_decode_backend import (
    RawStillDecodeBackend,
    _import_rawpy,
    _normalise_surface,
    _postprocess_raw,
    _qimage_from_array,
    probe_raw_source_identity,
)
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailGeometryState,
    DetailRenderRequest,
)


class _Token:
    def is_cancelled(self) -> bool:
        return False


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _request(path: Path, identity: AssetSourceIdentity) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=1,
        asset_id="raw-microbenchmark",
        source_identity=identity,
        viewport_physical_size=(1512, 982),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
    ).with_decode_level()


def run(path: Path) -> dict[str, object]:
    path = path.expanduser().absolute()
    stat = path.stat()
    unknown = AssetSourceIdentity.create(
        path,
        size_bytes=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
    )
    started = time.perf_counter()
    identity = probe_raw_source_identity(unknown)
    probe_ms = _elapsed_ms(started)

    request = _request(path, identity)
    started = time.perf_counter()
    surface = RawStillDecodeBackend().decode(request, _Token())
    embedded_ms = _elapsed_ms(started)

    started = time.perf_counter()
    compute_color_statistics(surface.image)
    color_stats_ms = _elapsed_ms(started)

    rawpy = _import_rawpy()
    if rawpy is None:
        raise RuntimeError("rawpy is unavailable")
    demosaic: dict[str, object] = {}
    with rawpy.imread(str(path)) as raw:
        for name, half_size in (("half", True), ("full", False)):
            started = time.perf_counter()
            rgb = _postprocess_raw(raw, half_size=half_size)
            postprocess_ms = _elapsed_ms(started)
            started = time.perf_counter()
            image = _qimage_from_array(rgb)
            bridge_ms = _elapsed_ms(started)
            started = time.perf_counter()
            normalised = _normalise_surface(image, QSize(4096, 4096))
            normalise_ms = _elapsed_ms(started)
            demosaic[name] = {
                "postprocess_ms": postprocess_ms,
                "bridge_ms": bridge_ms,
                "normalise_ms": normalise_ms,
                "decoded_size": [normalised.width(), normalised.height()],
            }

    return {
        "path_name": path.name,
        "source_size": [identity.width, identity.height],
        "selected_level": request.decode_level,
        "raw_probe_ms": probe_ms,
        "selected_backend_ms": embedded_ms,
        "selected_candidate": surface.fallback or "embedded",
        "surface_size": list(surface.decoded_size),
        "color_stats_ms": color_stats_ms,
        "demosaic": demosaic,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("tools/testbase/15/DSC_0291.NEF"),
    )
    args = parser.parse_args()
    print(json.dumps(run(args.path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
