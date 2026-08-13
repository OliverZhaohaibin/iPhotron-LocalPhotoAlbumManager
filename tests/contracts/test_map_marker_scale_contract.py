from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for map marker scale contracts",
    exc_type=ImportError,
)

from PySide6.QtCore import QObject, QPointF

from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.gui.ui.widgets.marker_controller import MarkerController, _ClusterWorker


pytestmark = pytest.mark.maps_scale_contract


class _CountingClusterWorker(_ClusterWorker):
    def __init__(self) -> None:
        super().__init__()
        self.project_calls = 0

    def _project_to_screen(self, *args, **kwargs):
        self.project_calls += 1
        return super()._project_to_screen(*args, **kwargs)


class _CountingExactMap:
    zoom = 2.0

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        self.project_calls = 0

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def prefers_exact_screen_projection(self) -> bool:
        return True

    def project_lonlat(self, lon: float, lat: float) -> QPointF:
        self.project_calls += 1
        x = (float(lon) + 180.0) / 360.0 * float(self._width)
        y = (90.0 - float(lat)) / 180.0 * float(self._height)
        return QPointF(x, y)


class _NullThumbnailLoader(QObject):
    def reset_for_album(self, root: Path) -> None:
        del root

    def invalidate(self, rel: str) -> None:
        del rel

    def request(self, *args, **kwargs):
        del args, kwargs
        return None


def test_map_clustering_scales_structurally_to_50k(tmp_path: Path) -> None:
    if os.environ.get("IPHOTO_RUN_MAPS_SCALE_CONTRACT") != "1":
        pytest.skip("large synthetic contract is run by the maps-scale-contract PR job")

    metrics = [
        _exercise_scale(tmp_path, count)
        for count in (1_000, 10_000, 50_000)
    ]

    for item in metrics:
        assert item["worker_projection_calls"] == item["asset_count"]
        assert item["native_projection_calls"] == item["coarse_cluster_count"]
        assert item["native_projection_calls"] < item["asset_count"]
        assert item["refined_asset_count"] == item["asset_count"]


def _exercise_scale(root: Path, count: int) -> dict[str, int]:
    width = 1_920
    height = 1_080
    assets = _synthetic_assets(root, count)
    map_widget = _CountingExactMap(width, height)
    controller = MarkerController(
        map_widget,
        _NullThumbnailLoader(),
        marker_size=72,
        thumbnail_size=192,
        provides_place_labels=True,
    )
    controller._view_zoom = 2.0
    threshold = controller._cluster_threshold(width, height)
    cell_size = max(math.ceil(threshold), 1)
    worker = _CountingClusterWorker()
    finished: list[list] = []
    worker.finished.connect(lambda _request_id, clusters: finished.append(list(clusters)))

    try:
        worker.build_clusters(
            1,
            assets,
            width,
            height,
            0.5,
            0.5,
            2.0,
            threshold,
            cell_size,
            72,
        )
        assert len(finished) == 1
        coarse_clusters = finished[0]
        refined = controller._refine_exact_projection_clusters(
            coarse_clusters,
            width=width,
            height=height,
            threshold=threshold,
            cell_size=cell_size,
            margin=72,
        )
    finally:
        controller.shutdown()

    return {
        "asset_count": count,
        "worker_projection_calls": worker.project_calls,
        "coarse_cluster_count": len(coarse_clusters),
        "native_projection_calls": map_widget.project_calls,
        "refined_asset_count": sum(len(cluster.assets) for cluster in refined),
    }


def _synthetic_assets(root: Path, count: int) -> list[GeotaggedAsset]:
    template = GeotaggedAsset(
        library_relative="0.jpg",
        album_relative="0.jpg",
        absolute_path=root / "0.jpg",
        album_path=root,
        asset_id="0",
        latitude=0.0,
        longitude=0.0,
        is_image=True,
        is_video=False,
        still_image_time=None,
        duration=None,
        location_name=None,
        live_photo_group_id=None,
        live_partner_rel=None,
    )
    return [
        replace(
            template,
            library_relative=f"{index}.jpg",
            album_relative=f"{index}.jpg",
            absolute_path=root / f"{index}.jpg",
            asset_id=str(index),
            longitude=-179.0 + 358.0 * float(index % 512) / 511.0,
            latitude=-80.0 + 160.0 * float((index // 512) % 256) / 255.0,
        )
        for index in range(count)
    ]
