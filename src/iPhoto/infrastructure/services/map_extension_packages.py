"""Pinned release payloads. Offline verification never contacts GitHub.

Changing these identities requires packaged map/search validation on the target
OS. The asset filenames are historical; they are not application version tags.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MapExtensionPackage:
    platform: str
    filename: str
    size: int
    sha256: str
    app_major: int = 6

    @property
    def url(self) -> str:
        return (
            "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
            f"releases/download/v5.0.0/{self.filename}"
        )


PACKAGES = (
    MapExtensionPackage(
        "win32",
        "extension.zip",
        538920928,
        "bebc4885c8c96c82f5701c5ffc6bb064ae53b6e9c42e523abdfe1c6d945506b8",
    ),
    MapExtensionPackage(
        "linux",
        "extension.tar.xz",
        423424596,
        "c24bb1527c37f47e22d36b45d191c1fbaf33ab683a3e941b2315fb7f97853427",
    ),
)
