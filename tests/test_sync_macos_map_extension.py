from __future__ import annotations

import importlib.util
import os
import subprocess
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


def test_collect_dependencies_resolves_copied_framework_transitive_deps_from_source_rpath(
    tmp_path,
) -> None:
    bin_root = tmp_path / "extension" / "bin"
    bin_root.mkdir(parents=True)
    helper = bin_root / "osmand_render_helper"
    helper.write_bytes(b"helper")

    qt_lib = tmp_path / "qt" / "lib"
    qt_gui = qt_lib / "QtGui.framework" / "Versions" / "A" / "QtGui"
    qt_dbus = qt_lib / "QtDBus.framework" / "Versions" / "A" / "QtDBus"
    qt_gui.parent.mkdir(parents=True)
    qt_dbus.parent.mkdir(parents=True)
    qt_gui.write_bytes(b"gui")
    qt_dbus.write_bytes(b"dbus")

    def runner(command):
        binary = Path(command[-1])
        if command[:2] == ("otool", "-L"):
            if binary == helper:
                stdout = f"""
{helper}:
\t@rpath/QtGui.framework/Versions/A/QtGui (compatibility version 6.0.0)
"""
            elif binary == qt_gui:
                stdout = f"""
{qt_gui}:
\t{qt_gui} (compatibility version 6.0.0)
\t@rpath/QtDBus.framework/Versions/A/QtDBus (compatibility version 6.0.0)
"""
            elif binary == qt_dbus:
                stdout = f"""
{qt_dbus}:
\t{qt_dbus} (compatibility version 6.0.0)
"""
            else:
                stdout = f"{binary}:\n"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[:2] == ("otool", "-l"):
            if binary == helper:
                stdout = f"""
Load command 1
          cmd LC_RPATH
      cmdsize 64
         path {qt_lib} (offset 12)
"""
            elif binary == qt_gui:
                stdout = """
Load command 1
          cmd LC_RPATH
      cmdsize 40
         path @loader_path/../../../ (offset 12)
"""
            else:
                stdout = ""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        raise AssertionError(f"unexpected command: {command}")

    result = sync_module.collect_and_copy_dependencies(
        runtime_binaries=(helper,),
        bin_root=bin_root,
        runner=runner,
    )

    copied = {path.relative_to(bin_root).as_posix() for path in result.copied_binaries}
    assert "QtGui.framework/Versions/A/QtGui" in copied
    assert "QtDBus.framework/Versions/A/QtDBus" in copied
    assert (bin_root / "QtDBus.framework" / "Versions" / "A" / "QtDBus").read_bytes() == b"dbus"


def test_repair_app_bundle_native_widget_links_rewrites_qt_frameworks(tmp_path) -> None:
    app_bundle = tmp_path / "main.app"
    macos_root = app_bundle / "Contents" / "MacOS"
    native_widget = (
        macos_root / "maps" / "tiles" / "extension" / "bin" / "osmand_native_widget.dylib"
    )
    native_widget.parent.mkdir(parents=True)
    native_widget.write_bytes(b"widget")
    for qt_name in ("QtCore", "QtGui", "QtWidgets"):
        (macos_root / qt_name).write_bytes(qt_name.encode("utf-8"))

    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        if command[:2] == ("otool", "-L"):
            stdout = f"""
{native_widget}:
\t@rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0)
\t@rpath/libX11.6.dylib (compatibility version 11.0.0)
\t@rpath/QtGui.framework/Versions/A/QtGui (compatibility version 6.0.0)
\t@rpath/QtWidgets.framework/Versions/A/QtWidgets (compatibility version 6.0.0)
"""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[0] in {"install_name_tool", "codesign"}:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    result = sync_module.repair_app_bundle_native_widget_qt_links(
        app_bundle,
        runner=runner,
    )

    assert result.native_widget == native_widget.resolve()
    assert result.rewritten_dependencies == (
        ("@rpath/QtCore.framework/Versions/A/QtCore", "@executable_path/QtCore"),
        ("@rpath/QtGui.framework/Versions/A/QtGui", "@executable_path/QtGui"),
        ("@rpath/QtWidgets.framework/Versions/A/QtWidgets", "@executable_path/QtWidgets"),
    )
    install_name_commands = [command for command in commands if command[0] == "install_name_tool"]
    assert install_name_commands == [
        (
            "install_name_tool",
            "-change",
            "@rpath/QtCore.framework/Versions/A/QtCore",
            "@executable_path/QtCore",
            str(native_widget.resolve()),
        ),
        (
            "install_name_tool",
            "-change",
            "@rpath/QtGui.framework/Versions/A/QtGui",
            "@executable_path/QtGui",
            str(native_widget.resolve()),
        ),
        (
            "install_name_tool",
            "-change",
            "@rpath/QtWidgets.framework/Versions/A/QtWidgets",
            "@executable_path/QtWidgets",
            str(native_widget.resolve()),
        ),
    ]
    assert not any("@rpath/libX11.6.dylib" in command for command in install_name_commands)
    assert any(command[0] == "codesign" for command in commands)


def test_repair_app_bundle_native_widget_links_fails_for_missing_app_qt_target(
    tmp_path,
) -> None:
    app_bundle = tmp_path / "main.app"
    macos_root = app_bundle / "Contents" / "MacOS"
    native_widget = (
        macos_root / "maps" / "tiles" / "extension" / "bin" / "osmand_native_widget.dylib"
    )
    native_widget.parent.mkdir(parents=True)
    native_widget.write_bytes(b"widget")

    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        if command[:2] == ("otool", "-L"):
            stdout = f"""
{native_widget}:
\t@rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0)
"""
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        raise AssertionError(f"unexpected command: {command}")

    try:
        sync_module.repair_app_bundle_native_widget_qt_links(app_bundle, runner=runner)
    except FileNotFoundError as exc:
        assert "QtCore" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing QtCore should fail the app bundle repair")

    assert [command[0] for command in commands] == ["otool"]
