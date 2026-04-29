from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from iPhoto import cli
from iPhoto.bootstrap.library_scan_service import AlbumReport


class _FakeScans:
    def __init__(self) -> None:
        self.scanned: list[tuple[Path, bool]] = []
        self.finalized: list[tuple[Path, list[dict]]] = []
        self.reported: list[Path] = []

    def scan_album(self, root: Path, *, persist_chunks: bool):
        self.scanned.append((root, persist_chunks))
        return SimpleNamespace(rows=[{"rel": "a.jpg"}])

    def finalize_scan(self, root: Path, rows: list[dict]) -> None:
        self.finalized.append((root, rows))

    def report_album(self, root: Path) -> AlbumReport:
        self.reported.append(root)
        return AlbumReport(title="Library", asset_count=2, live_pair_count=1)


class _FakeSession:
    def __init__(self) -> None:
        self.scans = _FakeScans()
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_cli_scan_uses_headless_session(monkeypatch, tmp_path: Path) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        cli,
        "create_headless_library_session",
        lambda root: session,
    )

    result = CliRunner().invoke(cli.app, ["scan", str(tmp_path)])

    assert result.exit_code == 0
    assert "Indexed 1 assets" in result.output
    assert session.scans.scanned == [(tmp_path, False)]
    assert session.scans.finalized == [(tmp_path, [{"rel": "a.jpg"}])]
    assert session.shutdown_called is True
    assert not hasattr(cli, "app_facade")
    assert not hasattr(cli, "get_global_repository")


def test_cli_report_uses_headless_session(monkeypatch, tmp_path: Path) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        cli,
        "create_headless_library_session",
        lambda root: session,
    )

    result = CliRunner().invoke(cli.app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    assert "Album: Library" in result.output
    assert "Assets: 2" in result.output
    assert "Live pairs: 1" in result.output
    assert session.scans.reported == [tmp_path]
    assert session.shutdown_called is True
