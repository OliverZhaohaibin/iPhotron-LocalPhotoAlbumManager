import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="checks macOS bundle signing")
def test_postbuild_signs_the_entrypoint_bundle_when_main_app_already_exists(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    driver = tmp_path / "scripts/build_nuitka_macos.sh"
    driver.parent.mkdir()
    shutil.copy2(source / "scripts" / driver.name, driver)
    (driver.parent / "sync_macos_map_extension.py").symlink_to(
        source / "scripts/sync_macos_map_extension.py"
    )
    (tmp_path / "tools").symlink_to(source / "tools")
    (tmp_path / "logo.png").symlink_to(source / "logo.png")
    native = tmp_path / "src/maps/tiles/extension/bin"
    native.mkdir(parents=True)
    for entry in ("iPhoto", "entrypoint.py", "maps/map_widget", "maps/style.json"):
        (tmp_path / "src" / entry).symlink_to(source / "src" / entry)

    # Exercise the real staging, dependency inspection, signing and manifest
    # steps using a small Mach-O fixture in place of a full Nuitka compilation.
    output = tmp_path / "release"
    old_bundle = output / "main.app"
    old_bundle.mkdir(parents=True)
    bundle = output / "entrypoint.app"
    binary = bundle / "Contents/MacOS/iPhotron"
    binary.parent.mkdir(parents=True)
    subprocess.run(
        ["clang", "-x", "c", "-o", str(binary), "-"],
        input=b"int main(void) { return 0; }",
        check=True,
    )
    shutil.copy2(binary, native / "osmand_render_helper")
    ort = bundle / "Contents/MacOS/onnxruntime/capi/onnxruntime_pybind11_state.so"
    ort.parent.mkdir(parents=True)
    shutil.copy2(binary, ort)
    subprocess.run(
        ["clang", "-dynamiclib", "-x", "c", "-o", str(native / "osmand_native_widget.dylib"), "-"],
        input=b"int map_fixture(void) { return 0; }",
        check=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": binary.name,
                "CFBundleIdentifier": "org.iphotron.packaging-test",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
            }
        )
    )
    python_driver = tmp_path / "build-python"
    recorded_args = tmp_path / "nuitka-args.txt"
    python_driver.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        '  -m) test "$2" = nuitka && printf "%s\\n" "$@" > "$PACKAGING_TEST_NUITKA_ARGS"; '
        'for argument in "$@"; do case "$argument" in --report=*) '
        'echo "<nuitka-report/>" > "${argument#--report=}" ;; esac; done ;;\n'
        '  -c) echo "$0.fixture" ;;\n'
        '  */recognition_packaging.py) if [ "$2" = environment ]; then exit 0; '
        'else exec "$PACKAGING_TEST_PYTHON" "$@"; fi ;;\n'
        '  *) exec "$PACKAGING_TEST_PYTHON" "$@" ;;\nesac\n',
        encoding="utf-8",
    )
    python_driver.chmod(0o755)
    subprocess.run(
        [
            "bash",
            str(driver),
            "--python",
            str(python_driver),
            "--output-dir",
            str(output),
            "--skip-aot",
            "--skip-map-runtime-sync",
        ],
        env={
            **os.environ,
            "ICON_PATH": "",
            "PACKAGING_TEST_PYTHON": sys.executable,
            "PACKAGING_TEST_NUITKA_ARGS": str(recorded_args),
        },
        check=True,
    )

    assert f"--macos-app-icon={tmp_path / 'logo.png'}" in recorded_args.read_text().splitlines()
    assert list(old_bundle.iterdir()) == []
    assert (bundle / "Contents/Resources/maps/extension.tar").is_file()
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    manifest = json.loads((output / "build-manifest.json").read_text())
    assert manifest["artifact_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
