"""Filesystem scanner adapter for the application scan use case."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ...application.ports import MediaScannerPort
from ...io.scanner_adapter import scan_album


class FilesystemMediaScanner(MediaScannerPort):
    """Adapter around the existing filesystem scanner implementation."""

    def scan(
        self,
        root: Path,
        include: Iterable[str],
        exclude: Iterable[str],
        *,
        existing_rows_resolver: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        generate_micro_thumbnails: bool | Callable[[], bool] = True,
    ) -> Iterator[dict[str, Any]]:
        scanner = scan_album(
            root,
            include,
            exclude,
            existing_rows_resolver=existing_rows_resolver,
            progress_callback=progress_callback,
            is_cancelled=is_cancelled,
            generate_micro_thumbnails=generate_micro_thumbnails,
        )
        try:
            yield from scanner
        finally:
            close = getattr(scanner, "close", None)
            if callable(close):
                close()


__all__ = ["FilesystemMediaScanner"]
