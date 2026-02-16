# aggregate_geo_data.py
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAssetRepository


@dataclass(frozen=True)
class GeoAssetInfo:
    asset_id: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    location_name: str = ""
    path: str = ""


@dataclass(frozen=True)
class AggregateGeoDataRequest(UseCaseRequest):
    album_id: str = ""


@dataclass(frozen=True)
class AggregateGeoDataResponse(UseCaseResponse):
    geotagged_assets: list[GeoAssetInfo] = field(default_factory=list)
    total_count: int = 0


class AggregateGeoDataUseCase(UseCase):
    """Collects geotagged assets and aggregates geographic data."""

    def __init__(self, asset_repo: IAssetRepository):
        self._asset_repo = asset_repo
        self._logger = logging.getLogger(__name__)

    def execute(self, request: AggregateGeoDataRequest) -> AggregateGeoDataResponse:
        assets = self._asset_repo.get_by_album(request.album_id)
        geo_assets = []
        for asset in assets:
            lat = asset.metadata.get("latitude") or asset.metadata.get("GPSLatitude")
            lon = asset.metadata.get("longitude") or asset.metadata.get("GPSLongitude")
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                except (ValueError, TypeError):
                    continue
                location = asset.metadata.get("location_name", "")
                geo_assets.append(GeoAssetInfo(
                    asset_id=asset.id,
                    latitude=lat_f,
                    longitude=lon_f,
                    location_name=location,
                    path=str(asset.path),
                ))
        return AggregateGeoDataResponse(
            geotagged_assets=geo_assets,
            total_count=len(geo_assets),
        )
