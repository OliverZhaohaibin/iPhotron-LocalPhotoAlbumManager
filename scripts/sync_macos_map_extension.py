#!/usr/bin/env python3
"""Sync the macOS OsmAnd runtime into the local maps extension folder."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

RESOURCE_DIRECTORIES = (
    "rendering_styles",
    "misc",
    "poi",
    "routing",
    "proj",
    "fonts",
    "fonts-telegram",
    "icons",
    "models",
    "color-palette",
    "countries-info",
)
MACOS_RUNTIME_FILES = (
    "osmand_render_helper",
    "osmand_native_widget.dylib",
)
SYSTEM_LIBRARY_PREFIXES = (
    "/System/Library/",
    "/usr/lib/",
)
RPATHS_FOR_EXTENSION_BIN = (
    "@loader_path",
    "@executable_path",
    "@loader_path/../../..",
)
QT_FRAMEWORK_INSTALL_NAME_PATTERN = re.compile(
    r"^@rpath/(Qt[^/]+)\.framework/Versions/A/(Qt[^/]+)$"
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DependencyCopyPlan:
    """Describe where a Mach-O dependency should be copied and loaded from."""

    source_root: Path
    source_binary: Path
    destination_root: Path
    destination_binary: Path
    install_name: str


@dataclass(frozen=True)
class DependencySyncResult:
    """Result from dependency collection."""

    dependency_map: dict[str, str]
    copied_binaries: tuple[Path, ...]


@dataclass(frozen=True)
class SyncResult:
    """Files staged into the local extension folder."""

    extension_root: Path
    copied_resources: tuple[Path, ...]
    runtime_binaries: tuple[Path, ...]
    copied_dependencies: tuple[Path, ...]


@dataclass(frozen=True)
class AppBundleRepairResult:
    """Qt dependency rewrites applied to the in-process native widget."""

    native_widget: Path
    rewritten_dependencies: tuple[tuple[str, str], ...]


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_sdk_root(repo_root: Path) -> Path:
    return repo_root.parent / "PySide6-OsmAnd-SDK"


def parse_otool_libraries(output: str) -> tuple[str, ...]:
    """Return install names from ``otool -L`` output."""

    libraries: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        install_name = stripped.split(" (", 1)[0].strip()
        if install_name:
            libraries.append(install_name)
    return tuple(libraries)


def parse_otool_rpaths(output: str) -> tuple[str, ...]:
    """Return LC_RPATH entries from ``otool -l`` output."""

    rpaths: list[str] = []
    in_rpath = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "cmd LC_RPATH":
            in_rpath = True
            continue
        if in_rpath and stripped.startswith("path "):
            value = stripped.removeprefix("path ").split(" (offset ", 1)[0].strip()
            if value:
                rpaths.append(value)
            in_rpath = False
    return tuple(rpaths)


def is_system_install_name(install_name: str | Path) -> bool:
    """Return whether an install name belongs to macOS system libraries."""

    value = str(install_name)
    return value.startswith(SYSTEM_LIBRARY_PREFIXES)


def dependency_copy_plan(source_path: Path, bin_root: Path) -> DependencyCopyPlan:
    """Return the extension destination for a non-system Mach-O dependency."""

    resolved_source = source_path.resolve()
    framework_root = _framework_root(resolved_source)
    if framework_root is not None:
        destination_root = bin_root / framework_root.name
        relative_binary = resolved_source.relative_to(framework_root)
        destination_binary = destination_root / relative_binary
        install_name = f"@rpath/{framework_root.name}/{relative_binary.as_posix()}"
        return DependencyCopyPlan(
            source_root=framework_root,
            source_binary=resolved_source,
            destination_root=destination_root,
            destination_binary=destination_binary,
            install_name=install_name,
        )

    destination_binary = bin_root / resolved_source.name
    return DependencyCopyPlan(
        source_root=resolved_source,
        source_binary=resolved_source,
        destination_root=destination_binary,
        destination_binary=destination_binary,
        install_name=f"@rpath/{resolved_source.name}",
    )


def sync_macos_map_extension(
    *,
    repo_root: Path,
    sdk_root: Path,
    extension_root: Path | None = None,
    fix_dependencies: bool = True,
    runner: CommandRunner | None = None,
) -> SyncResult:
    """Copy the SDK-built macOS runtime into ``src/maps/tiles/extension``."""

    resolved_repo = repo_root.resolve()
    resolved_sdk = sdk_root.resolve()
    resolved_extension = (
        extension_root.resolve()
        if extension_root is not None
        else resolved_repo / "src" / "maps" / "tiles" / "extension"
    )
    bin_root = resolved_extension / "bin"
    resources_root = resolved_sdk / "vendor" / "osmand" / "resources"
    dist_root = resolved_sdk / "tools" / "osmand_render_helper_native" / "dist-macosx"

    _assert_file(resolved_sdk / "src/maps/tiles/World_basemap_2.obf")
    _assert_file(resolved_sdk / "plugin/data/geonames.sqlite3")
    for name in RESOURCE_DIRECTORIES:
        _assert_dir(resources_root / name)
    for name in MACOS_RUNTIME_FILES:
        _assert_file(dist_root / name)

    resolved_extension.mkdir(parents=True, exist_ok=True)
    bin_root.mkdir(parents=True, exist_ok=True)

    copied_resources: list[Path] = []
    _copy_file(
        resolved_sdk / "src/maps/tiles/World_basemap_2.obf",
        resolved_extension / "World_basemap_2.obf",
    )
    copied_resources.append(resolved_extension / "World_basemap_2.obf")

    search_root = resolved_extension / "search"
    search_root.mkdir(parents=True, exist_ok=True)
    _copy_file(
        resolved_sdk / "plugin/data/geonames.sqlite3",
        search_root / "geonames.sqlite3",
    )
    copied_resources.append(search_root / "geonames.sqlite3")

    for name in RESOURCE_DIRECTORIES:
        destination = resolved_extension / name
        _copy_tree(resources_root / name, destination)
        copied_resources.append(destination)

    runtime_binaries: list[Path] = []
    for name in MACOS_RUNTIME_FILES:
        destination = bin_root / name
        _copy_file(dist_root / name, destination)
        runtime_binaries.append(destination)

    _make_executable(bin_root / "osmand_render_helper")

    dependency_result = DependencySyncResult(dependency_map={}, copied_binaries=())
    if fix_dependencies:
        active_runner = runner or _run
        dependency_result = collect_and_copy_dependencies(
            runtime_binaries=tuple(runtime_binaries),
            bin_root=bin_root,
            runner=active_runner,
        )
        patch_macho_dependencies(
            binaries=tuple(runtime_binaries) + dependency_result.copied_binaries,
            bin_root=bin_root,
            dependency_map=dependency_result.dependency_map,
            removable_root=resolved_sdk,
            runner=active_runner,
        )
        codesign_binaries(
            tuple(runtime_binaries) + dependency_result.copied_binaries,
            runner=active_runner,
        )

    return SyncResult(
        extension_root=resolved_extension,
        copied_resources=tuple(copied_resources),
        runtime_binaries=tuple(runtime_binaries),
        copied_dependencies=dependency_result.copied_binaries,
    )


def repair_app_bundle_native_widget_qt_links(
    app_bundle: Path,
    *,
    runner: CommandRunner | None = None,
) -> AppBundleRepairResult:
    """Rewrite the in-process native widget to use the app bundle's flat Qt libs.

    Nuitka's PySide6 plugin stages Qt libraries directly in
    ``Contents/MacOS/QtCore`` etc.  The OsmAnd map extension also contains
    ``Qt*.framework`` copies for its helper executable.  The helper runs in its
    own process and can keep those frameworks, but ``osmand_native_widget.dylib``
    is loaded into the main app process and must bind to the already-bundled Qt
    libraries to avoid duplicate Objective-C/Qt class registration.
    """

    resolved_app = app_bundle.resolve()
    macos_root = resolved_app / "Contents" / "MacOS"
    native_widget = (
        macos_root
        / "maps"
        / "tiles"
        / "extension"
        / "bin"
        / "osmand_native_widget.dylib"
    )
    _assert_file(native_widget)

    active_runner = runner or _run
    rewritten: list[tuple[str, str]] = []
    libraries = parse_otool_libraries(
        _capture(("otool", "-L", str(native_widget)), active_runner)
    )
    for install_name in libraries:
        replacement = flat_qt_install_name_for_framework(install_name)
        if replacement is None:
            continue

        qt_binary = replacement.removeprefix("@executable_path/")
        app_qt_binary = macos_root / qt_binary
        if not app_qt_binary.is_file():
            raise FileNotFoundError(
                f"Native widget Qt dependency target is missing: {app_qt_binary} "
                f"for {install_name}"
            )

        _run_tool(
            ("install_name_tool", "-change", install_name, replacement, str(native_widget)),
            active_runner,
        )
        rewritten.append((install_name, replacement))

    if rewritten:
        codesign_binaries((native_widget,), runner=active_runner)

    return AppBundleRepairResult(
        native_widget=native_widget,
        rewritten_dependencies=tuple(rewritten),
    )


def collect_and_copy_dependencies(
    *,
    runtime_binaries: tuple[Path, ...],
    bin_root: Path,
    runner: CommandRunner,
) -> DependencySyncResult:
    """Copy recursively resolved non-system Mach-O dependencies into ``bin``."""

    dependency_map: dict[str, str] = {}
    copied_binaries: list[Path] = []
    queued = [(binary.resolve(), binary.resolve()) for binary in runtime_binaries]
    inspected: set[Path] = set()
    staged_destinations: set[Path] = set()

    while queued:
        inspect_binary, linked_binary = queued.pop(0)
        binary = inspect_binary.resolve()
        linked_binary = linked_binary.resolve()
        if binary in inspected:
            continue
        inspected.add(binary)

        libraries = parse_otool_libraries(_capture(("otool", "-L", str(binary)), runner))
        rpaths = parse_otool_rpaths(_capture(("otool", "-l", str(binary)), runner))
        for install_name in libraries:
            if _is_self_install_name(linked_binary, bin_root, install_name):
                continue
            source = resolve_dependency_source(
                install_name,
                binary=binary,
                rpaths=rpaths,
            )
            if source is None or is_system_install_name(source):
                continue
            source = source.resolve()
            if source == binary:
                continue
            if _is_inside(source, bin_root):
                continue

            plan = dependency_copy_plan(source, bin_root)
            dependency_map[install_name] = plan.install_name
            destination_key = plan.destination_root.resolve()
            if destination_key not in staged_destinations:
                _copy_dependency(plan)
                _make_writable(plan.destination_binary)
                copied_binaries.append(plan.destination_binary)
                staged_destinations.add(destination_key)
            queued.append((plan.source_binary, plan.destination_binary))

    return DependencySyncResult(
        dependency_map=dependency_map,
        copied_binaries=tuple(_dedupe_paths(tuple(copied_binaries))),
    )


def flat_qt_install_name_for_framework(install_name: str) -> str | None:
    """Return the app-bundle flat Qt install name for a Qt framework dependency."""

    match = QT_FRAMEWORK_INSTALL_NAME_PATTERN.match(install_name)
    if match is None:
        return None
    framework_name, binary_name = match.groups()
    if framework_name != binary_name:
        return None
    return f"@executable_path/{binary_name}"


def resolve_dependency_source(
    install_name: str,
    *,
    binary: Path,
    rpaths: tuple[str, ...],
) -> Path | None:
    """Resolve an install name to a source path when it points outside system libs."""

    if is_system_install_name(install_name):
        return None
    if install_name.startswith("@rpath/"):
        suffix = install_name.removeprefix("@rpath/")
        for rpath in _ordered_rpaths_for_dependency(rpaths, suffix):
            base = _resolve_loader_token(rpath, binary)
            if base is None:
                continue
            candidate = base / suffix
            if candidate.exists():
                return candidate.resolve()
        return None
    if install_name.startswith("@loader_path/"):
        return (binary.parent / install_name.removeprefix("@loader_path/")).resolve()
    if install_name.startswith("@executable_path/"):
        return (binary.parent / install_name.removeprefix("@executable_path/")).resolve()
    if install_name.startswith("/"):
        return Path(install_name).resolve()
    return None


def patch_macho_dependencies(
    *,
    binaries: tuple[Path, ...],
    bin_root: Path,
    dependency_map: dict[str, str],
    removable_root: Path,
    runner: CommandRunner,
) -> None:
    """Rewrite copied Mach-O files so they load from the extension bin folder."""

    for binary in _dedupe_paths(binaries):
        _make_writable(binary)
        libraries = parse_otool_libraries(_capture(("otool", "-L", str(binary)), runner))
        for install_name in libraries:
            replacement = dependency_map.get(install_name)
            if replacement and replacement != install_name:
                _run_tool(
                    ("install_name_tool", "-change", install_name, replacement, str(binary)),
                    runner,
                )

        if _can_have_install_id(binary, bin_root):
            _run_tool(
                (
                    "install_name_tool",
                    "-id",
                    _install_name_for_binary(binary, bin_root),
                    str(binary),
                ),
                runner,
            )

        existing_rpaths = parse_otool_rpaths(_capture(("otool", "-l", str(binary)), runner))
        for rpath in RPATHS_FOR_EXTENSION_BIN:
            if rpath not in existing_rpaths:
                _run_tool(
                    ("install_name_tool", "-add_rpath", rpath, str(binary)),
                    runner,
                    check=False,
                )
        for rpath in existing_rpaths:
            if _should_remove_rpath(rpath, removable_root):
                _run_tool(
                    ("install_name_tool", "-delete_rpath", rpath, str(binary)),
                    runner,
                    check=False,
                )


def codesign_binaries(binaries: tuple[Path, ...], *, runner: CommandRunner) -> None:
    """Apply ad-hoc signatures after mutating Mach-O load commands."""

    for binary in _dedupe_paths(binaries):
        _run_tool(("codesign", "--force", "--sign", "-", str(binary)), runner, check=False)


def _copy_dependency(plan: DependencyCopyPlan) -> None:
    if plan.source_root.is_dir():
        if plan.destination_root.exists():
            shutil.rmtree(plan.destination_root)
        shutil.copytree(plan.source_root, plan.destination_root, symlinks=True)
        return
    _copy_file(plan.source_root, plan.destination_root)


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _assert_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required file is missing: {path}")


def _assert_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Required directory is missing: {path}")


def _framework_root(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if parent.suffix == ".framework":
            return parent
    return None


def _resolve_loader_token(value: str, binary: Path) -> Path | None:
    if value.startswith("@loader_path"):
        suffix = value.removeprefix("@loader_path").lstrip("/")
        return (binary.parent / suffix).resolve()
    if value.startswith("@executable_path"):
        suffix = value.removeprefix("@executable_path").lstrip("/")
        return (binary.parent / suffix).resolve()
    if value.startswith("/"):
        return Path(value).resolve()
    return None


def _ordered_rpaths_for_dependency(rpaths: tuple[str, ...], suffix: str) -> tuple[str, ...]:
    if suffix.startswith("Qt") and ".framework/" in suffix:
        return tuple(sorted(rpaths, key=lambda item: 0 if "PySide6/Qt/lib" in item else 1))
    return rpaths


def _install_name_for_binary(binary: Path, bin_root: Path) -> str:
    relative = binary.resolve().relative_to(bin_root.resolve()).as_posix()
    return f"@rpath/{relative}"


def _can_have_install_id(binary: Path, bin_root: Path) -> bool:
    relative_parts = binary.resolve().relative_to(bin_root.resolve()).parts
    return binary.suffix == ".dylib" or any(part.endswith(".framework") for part in relative_parts)


def _is_self_install_name(binary: Path, bin_root: Path, install_name: str) -> bool:
    try:
        return install_name == _install_name_for_binary(binary, bin_root)
    except ValueError:
        return False


def _should_remove_rpath(rpath: str, removable_root: Path) -> bool:
    if rpath.startswith("@"):
        return False
    removable_value = str(removable_root.resolve())
    return (
        rpath.startswith(removable_value)
        or "/.venv/" in rpath
        or "/PySide6/Qt/lib" in rpath
        or rpath.startswith("/opt/homebrew")
        or rpath.startswith("/usr/local/opt")
    )


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWUSR)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _dedupe_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def _capture(command: Sequence[str], runner: CommandRunner) -> str:
    return _run_tool(command, runner).stdout


def _run_tool(
    command: Sequence[str],
    runner: CommandRunner,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = runner(tuple(command))
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr.strip()}"
        )
    return result


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync PySide6-OsmAnd-SDK macOS runtime into src/maps/tiles/extension.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_repo_root_from_script(),
        help="iPhotron repository root. Defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--sdk-root",
        type=Path,
        default=None,
        help="PySide6-OsmAnd-SDK checkout. Defaults to a sibling checkout.",
    )
    parser.add_argument(
        "--extension-root",
        type=Path,
        default=None,
        help="Extension root to populate. Defaults to src/maps/tiles/extension.",
    )
    parser.add_argument(
        "--skip-dependency-fix",
        action="store_true",
        help="Only copy files; skip otool/install_name_tool/codesign dependency repair.",
    )
    parser.add_argument(
        "--repair-app-bundle",
        type=Path,
        default=None,
        help=(
            "Rewrite the staged app bundle's in-process OsmAnd widget to link "
            "against the app bundle's flat Qt libraries, then exit."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.repair_app_bundle is not None:
        if sys.platform != "darwin":
            parser.error("App bundle Qt dependency repair requires macOS.")
        result = repair_app_bundle_native_widget_qt_links(args.repair_app_bundle)
        print(f"Repaired native widget Qt links: {result.native_widget}")
        print(f"Dependencies rewritten: {len(result.rewritten_dependencies)}")
        return 0

    if sys.platform != "darwin" and not args.skip_dependency_fix:
        parser.error(
            "Mach-O dependency repair requires macOS. Use --skip-dependency-fix to copy only."
        )

    repo_root = args.repo_root.resolve()
    sdk_root = (
        args.sdk_root.resolve()
        if args.sdk_root is not None
        else _default_sdk_root(repo_root).resolve()
    )
    result = sync_macos_map_extension(
        repo_root=repo_root,
        sdk_root=sdk_root,
        extension_root=args.extension_root,
        fix_dependencies=not args.skip_dependency_fix,
    )

    print(f"Synced macOS map extension: {result.extension_root}")
    print(f"Resources copied: {len(result.copied_resources)}")
    print(f"Runtime binaries copied: {len(result.runtime_binaries)}")
    print(f"Dependencies copied: {len(result.copied_dependencies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
