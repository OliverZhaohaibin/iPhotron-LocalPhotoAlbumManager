from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_sync_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "sync_macos_map_extension.py"
    spec = importlib.util.spec_from_file_location("sync_macos_map_extension", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_module = _load_sync_module()


def _create_fake_sdk(root: Path) -> None:
    (root / "src" / "maps" / "tiles").mkdir(parents=True)
    (root / "src" / "maps" / "tiles" / "World_basemap_2.obf").write_bytes(b"obf")
    (root / "plugin" / "data").mkdir(parents=True)
    (root / "plugin" / "data" / "geonames.sqlite3").write_bytes(b"sqlite")

    resources_root = root / "vendor" / "osmand" / "resources"
    for name in sync_module.RESOURCE_DIRECTORIES:
        directory = resources_root / name
        directory.mkdir(parents=True)
        (directory / "marker.txt").write_text(name, encoding="utf-8")

    dist_root = root / "tools" / "osmand_render_helper_native" / "dist-macosx"
    dist_root.mkdir(parents=True)
    (dist_root / "osmand_render_helper").write_bytes(b"helper")
    (dist_root / "osmand_native_widget.dylib").write_bytes(b"dylib")


def test_sync_macos_map_extension_copies_expected_layout_without_dependency_fix(tmp_path) -> None:
    repo_root = tmp_path / "iPhotron"
    sdk_root = tmp_path / "PySide6-OsmAnd-SDK"
    repo_root.mkdir()
    _create_fake_sdk(sdk_root)

    result = sync_module.sync_macos_map_extension(
        repo_root=repo_root,
        sdk_root=sdk_root,
        fix_dependencies=False,
    )
    extension_root = repo_root / "src" / "maps" / "tiles" / "extension"

    assert result.extension_root == extension_root.resolve()
    assert (extension_root / "World_basemap_2.obf").read_bytes() == b"obf"
    assert (extension_root / "search" / "geonames.sqlite3").read_bytes() == b"sqlite"
    for name in sync_module.RESOURCE_DIRECTORIES:
        assert (extension_root / name / "marker.txt").read_text(encoding="utf-8") == name

    helper = extension_root / "bin" / "osmand_render_helper"
    assert helper.read_bytes() == b"helper"
    assert os.access(helper, os.X_OK)
    assert (extension_root / "bin" / "osmand_native_widget.dylib").read_bytes() == b"dylib"
    assert result.copied_dependencies == ()


def test_otool_parsers_and_dependency_filtering() -> None:
    libraries = sync_module.parse_otool_libraries(
        """
/tmp/bin/osmand_render_helper:
\t@rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0)
\t/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa (compatibility version 1.0.0)
\t/opt/homebrew/opt/libx11/lib/libX11.6.dylib (compatibility version 11.0.0)
""",
    )
    rpaths = sync_module.parse_otool_rpaths(
        """
Load command 1
          cmd LC_RPATH
      cmdsize 40
         path @loader_path (offset 12)
Load command 2
          cmd LC_RPATH
      cmdsize 128
         path /tmp/PySide6/Qt/lib (offset 12)
""",
    )

    assert libraries == (
        "@rpath/QtCore.framework/Versions/A/QtCore",
        "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
        "/opt/homebrew/opt/libx11/lib/libX11.6.dylib",
    )
    assert rpaths == ("@loader_path", "/tmp/PySide6/Qt/lib")
    assert sync_module.is_system_install_name(
        "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa"
    )
    assert sync_module.is_system_install_name("/usr/lib/libSystem.B.dylib")
    assert not sync_module.is_system_install_name("/opt/homebrew/opt/libx11/lib/libX11.6.dylib")


def test_dependency_copy_plan_maps_frameworks_and_dylibs_to_extension_bin(tmp_path) -> None:
    bin_root = tmp_path / "extension" / "bin"
    qt_binary = (
        tmp_path / "PySide6" / "Qt" / "lib" / "QtCore.framework" / "Versions" / "A" / "QtCore"
    )
    x11_binary = tmp_path / "homebrew" / "lib" / "libX11.6.dylib"

    qt_plan = sync_module.dependency_copy_plan(qt_binary, bin_root)
    x11_plan = sync_module.dependency_copy_plan(x11_binary, bin_root)

    assert qt_plan.source_root == tmp_path / "PySide6" / "Qt" / "lib" / "QtCore.framework"
    assert qt_plan.destination_root == bin_root / "QtCore.framework"
    assert qt_plan.destination_binary == bin_root / "QtCore.framework" / "Versions" / "A" / "QtCore"
    assert qt_plan.install_name == "@rpath/QtCore.framework/Versions/A/QtCore"
    assert x11_plan.destination_binary == bin_root / "libX11.6.dylib"
    assert x11_plan.install_name == "@rpath/libX11.6.dylib"


def test_resolve_dependency_source_prefers_pyside6_qt_rpath_for_qt_frameworks(tmp_path) -> None:
    binary = tmp_path / "extension" / "bin" / "osmand_render_helper"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"helper")
    homebrew_qt = tmp_path / "homebrew" / "qt" / "lib" / "QtCore.framework" / "Versions" / "A"
    pyside_qt = tmp_path / "PySide6" / "Qt" / "lib" / "QtCore.framework" / "Versions" / "A"
    homebrew_qt.mkdir(parents=True)
    pyside_qt.mkdir(parents=True)
    (homebrew_qt / "QtCore").write_bytes(b"homebrew")
    (pyside_qt / "QtCore").write_bytes(b"pyside")

    source = sync_module.resolve_dependency_source(
        "@rpath/QtCore.framework/Versions/A/QtCore",
        binary=binary,
        rpaths=(
            str(tmp_path / "homebrew" / "qt" / "lib"),
            str(tmp_path / "PySide6" / "Qt" / "lib"),
        ),
    )

    assert source == (pyside_qt / "QtCore").resolve()
