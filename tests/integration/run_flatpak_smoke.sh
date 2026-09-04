#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
APP_ID="io.github.oliverzhaohaibin.iPhotron"
SMOKE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/iphotron-flatpak-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_ROOT"' EXIT

export XDG_DATA_HOME="$SMOKE_ROOT/xdg-data"
export XDG_CACHE_HOME="$SMOKE_ROOT/xdg-cache"
export XDG_CONFIG_HOME="$SMOKE_ROOT/xdg-config"
export XDG_RUNTIME_DIR="$SMOKE_ROOT/xdg-runtime"
mkdir -p "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

STANDALONE_DIR="$SMOKE_ROOT/entrypoint.dist"
mkdir -p \
  "$STANDALONE_DIR/iPhoto/resources/i18n" \
  "$STANDALONE_DIR/iPhoto/gui/ui/widgets" \
  "$STANDALONE_DIR/maps/tiles/extension/bin" \
  "$STANDALONE_DIR/PySide6/Qt/plugins/platforms"

cc -x c -o "$STANDALONE_DIR/entrypoint.bin" - <<'EOF'
#include <stdio.h>
int main(void) {
    puts("IPHOTRON_FLATPAK_SMOKE_OK");
    return 0;
}
EOF
printf 'qsb' > "$STANDALONE_DIR/iPhoto/gui/ui/widgets/image.qsb"
printf '#!/bin/sh\nexit 0\n' > "$STANDALONE_DIR/maps/tiles/extension/bin/osmand_render_helper"
chmod 755 "$STANDALONE_DIR/maps/tiles/extension/bin/osmand_render_helper"
printf 'qt' > "$STANDALONE_DIR/PySide6/Qt/plugins/platforms/libqxcb.so"

ICON_PATH="$SMOKE_ROOT/iphotron.png"
python3 - "$ICON_PATH" <<'PY'
import struct
import sys
import zlib
from pathlib import Path


def chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


width = height = 256
scanlines = b"".join(b"\x00" + (b"\x1f\x6f\xc9\xff" * width) for _ in range(height))
Path(sys.argv[1]).write_bytes(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(scanlines))
    + chunk(b"IEND", b"")
)
PY

STANDALONE_MANIFEST="$SMOKE_ROOT/build-manifest.json"
python3 "$ROOT_DIR/tools/build_manifest.py" \
  --root "$ROOT_DIR" \
  --artifact "$STANDALONE_DIR/entrypoint.bin" \
  --artifact-tree "$STANDALONE_DIR" \
  --build-driver "$ROOT_DIR/scripts/build_nuitka_fast.sh" \
  --build-flag "profile=flatpak-ci-smoke" \
  --native-runtime "$STANDALONE_DIR/maps/tiles/extension/bin" \
  --asset "$STANDALONE_DIR/maps/tiles" \
  --asset "$STANDALONE_DIR/iPhoto/resources/i18n" \
  --output "$STANDALONE_MANIFEST"

desktop-file-validate \
  "$ROOT_DIR/packaging/flatpak/io.github.oliverzhaohaibin.iPhotron.desktop"
appstreamcli validate --explain \
  "$ROOT_DIR/packaging/flatpak/io.github.oliverzhaohaibin.iPhotron.metainfo.xml"

flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo

VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
OUTPUT_PATH="$SMOKE_ROOT/${APP_ID}-${VERSION}-x86_64.flatpak"
bash "$ROOT_DIR/scripts/build_flatpak.sh" \
  --standalone-dir "$STANDALONE_DIR" \
  --standalone-manifest "$STANDALONE_MANIFEST" \
  --icon "$ICON_PATH" \
  --output "$OUTPUT_PATH"

flatpak install --user --noninteractive "$OUTPUT_PATH"
RUN_OUTPUT="$(flatpak run "$APP_ID")"
[[ "$RUN_OUTPUT" == *"IPHOTRON_FLATPAK_SMOKE_OK"* ]] || {
  echo "error: installed Flatpak did not run the staged executable" >&2
  exit 1
}
flatpak uninstall --user --noninteractive "$APP_ID"

test -s "$OUTPUT_PATH"
test -s "${OUTPUT_PATH}.abi-report.json"
test -s "${OUTPUT_PATH}.build-manifest.json"
echo "Flatpak build/install/run smoke passed"
