"""Typer-based CLI entry point."""

from __future__ import annotations

from functools import wraps
from pathlib import Path
import sys

import typer
from rich import print

if __package__ in (None, ""):
    package_root = Path(__file__).resolve().parent.parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    from iPhoto.bootstrap.library_session import create_headless_library_session  # type: ignore  # pragma: no cover
    from iPhoto.errors import (
        AlbumNotFoundError,
        IPhotoError,
        LockTimeoutError,
        ManifestInvalidError,
    )  # type: ignore  # pragma: no cover
    from iPhoto.models.album import Album  # type: ignore  # pragma: no cover
else:
    from .bootstrap.library_session import create_headless_library_session
    from .errors import AlbumNotFoundError, IPhotoError, LockTimeoutError, ManifestInvalidError
    from .models.album import Album

app = typer.Typer(help="Folder-native photo manager with Live Photo support")
cover_app = typer.Typer(help="Manage album covers")
feature_app = typer.Typer(help="Manage featured assets")
app.add_typer(cover_app, name="cover")
app.add_typer(feature_app, name="feature")


def _handle_errors(func):
    @wraps(func)
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


def _require_scan_service(session):
    scan_service = session.scans
    if scan_service is None:
        raise IPhotoError("Library scan service is unavailable.")
    return scan_service


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

    session = create_headless_library_session(album_dir)
    try:
        scan_service = _require_scan_service(session)
        result = scan_service.scan_album(album_dir, persist_chunks=False)
        scan_service.finalize_scan(album_dir, result.rows)
    finally:
        session.shutdown()
    print(f"[green]Indexed {len(result.rows)} assets")


@app.command()
@_handle_errors
def pair(album_dir: Path = typer.Argument(Path.cwd(), exists=True)) -> None:
    """Rebuild Live Photo pairings."""

    session = create_headless_library_session(album_dir)
    try:
        scan_service = _require_scan_service(session)
        groups = scan_service.pair_album(album_dir)
    finally:
        session.shutdown()
    print(f"[green]Paired {len(groups)} Live Photos")


@cover_app.command("set")
@_handle_errors
def cover_set(album_dir: Path, rel: str) -> None:
    """Set the album cover to the provided relative path."""

    album = Album.open(album_dir)
    album.set_cover(rel)
    album.save()
    print(f"[green]Set cover to {rel}")


@feature_app.command("add")
@_handle_errors
def feature_add(album_dir: Path, ref: str) -> None:
    """Add an item to the featured list."""

    album = Album.open(album_dir)
    album.add_featured(ref)
    album.save()
    print(f"[green]Added featured {ref}")


@feature_app.command("rm")
@_handle_errors
def feature_rm(album_dir: Path, ref: str) -> None:
    """Remove an item from the featured list."""

    album = Album.open(album_dir)
    album.remove_featured(ref)
    album.save()
    print(f"[green]Removed featured {ref}")


@app.command()
@_handle_errors
def report(album_dir: Path = typer.Argument(Path.cwd(), exists=True)) -> None:
    """Print a simple album report."""

    session = create_headless_library_session(album_dir)
    try:
        scan_service = _require_scan_service(session)
        album_report = scan_service.report_album(album_dir)
    finally:
        session.shutdown()
    print(
        f"Album: {album_report.title or album_dir.name}\n"
        f"Assets: {album_report.asset_count}\n"
        f"Live pairs: {album_report.live_pair_count}"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
