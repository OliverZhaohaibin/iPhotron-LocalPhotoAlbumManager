from __future__ import annotations

import math
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for map marker scale contracts",
    exc_type=ImportError,
)

from PySide6.QtCore import QObject, QPointF
from PySide6.QtWidgets import QApplication

from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.gui.ui.widgets.marker_controller import (
    MarkerController,
    _ClusterWorker,
    _MarkerCluster,
)


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


class _FixedExactMap(_CountingExactMap):
    def __init__(self, point: QPointF) -> None:
        super().__init__(800, 600)
        self._point = point

    def project_lonlat(self, lon: float, lat: float) -> QPointF:
        del lon, lat
        self.project_calls += 1
        return QPointF(self._point)


class _NullThumbnailLoader(QObject):
    def reset_for_album(self, root: Path) -> None:
        del root

    def invalidate(self, rel: str) -> None:
        del rel

    def request(self, *args, **kwargs):
        del args, kwargs
        return None


def test_map_clustering_scales_structurally_to_50k(
    tmp_path: Path,
) -> None:
    _require_scale_contract()

    metrics = [
        _exercise_scale(tmp_path, count)
        for count in (1_000, 10_000, 50_000)
    ]

    exact_item = metrics[0]
    assert exact_item["worker_projection_calls"] == 0
    assert exact_item["native_projection_calls"] == exact_item["asset_count"]
    assert exact_item["refined_asset_count"] == exact_item["asset_count"]

    for item in metrics[1:]:
        assert item["worker_projection_calls"] == item["asset_count"]
        assert item["native_projection_calls"] == item["coarse_cluster_count"]
        assert item["native_projection_calls"] < item["asset_count"]
        assert item["refined_asset_count"] == item["asset_count"]


def test_exact_refinement_preserves_coarse_centroid_contract(tmp_path: Path) -> None:
    _require_scale_contract()
    assets = _synthetic_assets(tmp_path, 2)
    map_widget = _FixedExactMap(QPointF(110.0, 100.0))
    controller = MarkerController(
        map_widget,
        _NullThumbnailLoader(),
        marker_size=72,
        thumbnail_size=192,
        provides_place_labels=True,
    )
    coarse_cluster = _MarkerCluster(
        representative=assets[0],
        assets=assets,
        screen_pos=QPointF(200.0, 100.0),
        representative_screen_pos_approx=QPointF(100.0, 100.0),
    )

    try:
        refined = controller._refine_exact_projection_clusters(
            [coarse_cluster],
            width=800,
            height=600,
            threshold=271.0,
            cell_size=271,
            margin=72,
        )
    finally:
        controller.shutdown()

    assert len(refined) == 1
    assert refined[0].screen_pos == QPointF(210.0, 100.0)
    assert refined[0].screen_pos != QPointF(110.0, 100.0)


def test_production_dispatch_is_geometrically_stable_at_1000_boundary(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    _require_scale_contract()
    exact_assets = _boundary_assets(tmp_path, 1_000)
    hybrid_assets = _boundary_assets(tmp_path, 1_001)

    exact_clusters, exact_calls = _run_production_dispatch(
        tmp_path,
        exact_assets,
        qapp,
    )
    hybrid_clusters, hybrid_calls = _run_production_dispatch(
        tmp_path,
        hybrid_assets,
        qapp,
    )

    assert exact_calls == 1_000
    assert hybrid_calls == 1
    assert len(exact_clusters) == len(hybrid_clusters) == 1
    assert len(exact_clusters[0].assets) == 1_000
    assert len(hybrid_clusters[0].assets) == 1_001
    assert abs(exact_clusters[0].screen_pos.x() - hybrid_clusters[0].screen_pos.x()) < 1.0
    assert hybrid_clusters[0].screen_pos.x() > 150.0


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
    if count <= MarkerController.EXACT_PROJECTION_ASSET_LIMIT:
        controller._assets = assets
        controller._library_root = root
        try:
            controller._rebuild_photo_clusters()
            clusters = list(controller._clusters)
        finally:
            controller.shutdown()
        return {
            "asset_count": count,
            "worker_projection_calls": 0,
            "coarse_cluster_count": len(clusters),
            "native_projection_calls": map_widget.project_calls,
            "refined_asset_count": sum(len(cluster.assets) for cluster in clusters),
        }

    threshold = controller._cluster_threshold(
        width,
        height,
        density_adaptive=True,
    )
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


def _run_production_dispatch(
    root: Path,
    assets: list[GeotaggedAsset],
    qapp: QApplication,
) -> tuple[list[_MarkerCluster], int]:
    map_widget = _CountingExactMap(1_024, 600)
    controller = MarkerController(
        map_widget,
        _NullThumbnailLoader(),
        marker_size=72,
        thumbnail_size=192,
        provides_place_labels=True,
    )
    controller._assets = assets
    controller._library_root = root
    controller._view_center_x = 0.5
    controller._view_center_y = 0.5
    controller._view_zoom = 2.0

    try:
        controller._rebuild_photo_clusters()
        deadline = time.monotonic() + 5.0
        while (
            controller._cluster_request_context is not None
            and time.monotonic() < deadline
        ):
            qapp.processEvents()
            time.sleep(0.005)
        assert controller._cluster_request_context is None
        return list(controller._clusters), map_widget.project_calls
    finally:
        controller.shutdown()


def _boundary_assets(root: Path, count: int) -> list[GeotaggedAsset]:
    assets = _synthetic_assets(root, count)
    longitudes = (-144.84375, -74.53125)
    return [
        replace(
            asset,
            longitude=longitudes[index % 2],
            latitude=0.0,
        )
        for index, asset in enumerate(assets)
    ]


def _require_scale_contract() -> None:
    if os.environ.get("IPHOTO_RUN_MAPS_SCALE_CONTRACT") != "1":
        pytest.skip("large synthetic contract is run by the maps-scale-contract PR job")


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
