"""Typer-based CLI entry point."""

from __future__ import annotations

from pathlib import Path
import sys

import typer
from rich import print

# Setup path for script execution
if __package__ in (None, ""):
    package_root = Path(__file__).resolve().parent.parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

from src.iPhoto.config import WORK_DIR_NAME
from src.iPhoto.errors import (
    AlbumNotFoundError,
    IPhotoError,
    LockTimeoutError,
    ManifestInvalidError,
)
from src.iPhoto.models.album import Album
from src.iPhoto.appctx import _create_di_container
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.domain.repositories import IAssetRepository
from src.iPhoto.domain.models.query import AssetQuery

# Initialize Services
container = _create_di_container()
album_service = container.resolve(AlbumService)
asset_repo = container.resolve(IAssetRepository)

app = typer.Typer(help="Folder-native photo manager with Live Photo support")
cover_app = typer.Typer(help="Manage album covers")
feature_app = typer.Typer(help="Manage featured assets")
app.add_typer(cover_app, name="cover")
app.add_typer(feature_app, name="feature")


def _handle_errors(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (AlbumNotFoundError, ManifestInvalidError, LockTimeoutError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        except IPhotoError as exc:
            typer.echo(f"Unexpected error: {exc}", err=True)
            raise typer.Exit(1) from exc

    return wrapper


@app.command()
@_handle_errors
def init(album_dir: Path = typer.Argument(Path.cwd(), exists=False)) -> None:
    """Initialise an album manifest if it does not exist."""

    album_dir.mkdir(parents=True, exist_ok=True)
    album = Album.open(album_dir)
    album.save()
    print(f"[green]Initialised album at {album_dir}")


@app.command()
@_handle_errors
def scan(album_dir: Path = typer.Argument(Path.cwd(), exists=True)) -> None:
    """Scan files and update the index cache."""

    # We need album ID. Open album first.
    resp = album_service.open_album(album_dir)
    scan_resp = album_service.scan_album(resp.album_id, force_rescan=True)
    print(f"[green]Indexed {scan_resp.added_count + scan_resp.updated_count} assets")


@app.command()
@_handle_errors
def pair(album_dir: Path = typer.Argument(Path.cwd(), exists=True)) -> None:
    """Rebuild Live Photo pairings."""

    resp = album_service.open_album(album_dir)
    pair_resp = album_service.pair_live_photos(resp.album_id)
    print(f"[green]Paired {pair_resp.paired_count} Live Photos")


@cover_app.command("set")
@_handle_errors
def cover_set(album_dir: Path, rel: str) -> None:
    """Set the album cover to the provided relative path."""

    # Use legacy Album model for manifest manipulation
    album = Album.open(album_dir)
    if "cover" not in album.manifest:
        album.manifest["cover"] = {}
    album.manifest["cover"]["rel"] = rel
    album.save()
    print(f"[green]Set cover to {rel}")


@feature_app.command("add")
@_handle_errors
def feature_add(album_dir: Path, ref: str) -> None:
    """Add an item to the featured list."""

    album = Album.open(album_dir)
    if "featured" not in album.manifest:
        album.manifest["featured"] = []
    if ref not in album.manifest["featured"]:
        album.manifest["featured"].append(ref)
    album.save()
    print(f"[green]Added featured {ref}")


@feature_app.command("rm")
@_handle_errors
def feature_rm(album_dir: Path, ref: str) -> None:
    """Remove an item from the featured list."""

    album = Album.open(album_dir)
    if "featured" in album.manifest:
        if ref in album.manifest["featured"]:
            album.manifest["featured"].remove(ref)
    album.save()
    print(f"[green]Removed featured {ref}")


@app.command()
@_handle_errors
def report(album_dir: Path = typer.Argument(Path.cwd(), exists=True)) -> None:
    """Print a simple album report."""

    resp = album_service.open_album(album_dir)
    # Use repo to count
    query = AssetQuery().with_album_id(resp.album_id)
    count = asset_repo.count(query)

    # We don't have easy access to live pairs count from repo without specific query or parsing JSON
    # For now, just print asset count.

    print(
        f"Album: {resp.title}\n"
        f"Assets: {count}\n"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
