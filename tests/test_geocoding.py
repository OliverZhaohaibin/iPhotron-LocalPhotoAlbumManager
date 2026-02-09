import sys
import types


stub_module = types.ModuleType("reverse_geocoder")
stub_module.RGeocoder = object
sys.modules.setdefault("reverse_geocoder", stub_module)

from iPhoto.utils import geocoding


def test_resolve_location_name_accepts_latitude_longitude_strings(monkeypatch):
    class _StubGeocoder:
        def query(self, _coords):
            return [{"name": "London", "admin1": "England"}]

    monkeypatch.setattr(geocoding, "_geocoder", lambda: _StubGeocoder())

    result = geocoding.resolve_location_name({"latitude": "51.5074", "longitude": "-0.1278"})

    assert result == "London — England"
