#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/build_appimage.sh --standalone-dir DIR --icon PNG --output FILE [--appimagetool PATH]"
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STANDALONE_DIR=""
ICON_PATH=""
OUTPUT_PATH=""
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --standalone-dir)
      STANDALONE_DIR="$2"
      shift 2
      ;;
    --icon)
      ICON_PATH="$2"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --appimagetool)
      APPIMAGETOOL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || { echo "error: AppImage builds require Linux" >&2; exit 2; }
[[ -n "$STANDALONE_DIR" && -d "$STANDALONE_DIR" ]] || { echo "error: invalid --standalone-dir" >&2; exit 2; }
[[ -n "$ICON_PATH" && -f "$ICON_PATH" ]] || { echo "error: --icon must name an existing PNG" >&2; exit 2; }
[[ "$ICON_PATH" == *.png ]] || { echo "error: AppImage icon must be a PNG" >&2; exit 2; }
[[ -n "$OUTPUT_PATH" ]] || { echo "error: --output is required" >&2; exit 2; }
command -v "$APPIMAGETOOL" >/dev/null 2>&1 || { echo "error: appimagetool not found" >&2; exit 2; }

ENTRYPOINT=""
for candidate in main.bin main; do
  if [[ -x "$STANDALONE_DIR/$candidate" ]]; then
    ENTRYPOINT="$candidate"
    break
  fi
done
[[ -n "$ENTRYPOINT" ]] || { echo "error: standalone entry point main.bin/main not found" >&2; exit 2; }

find "$STANDALONE_DIR" -type f -name '*.qsb' -print -quit | grep -q . || {
  echo "error: standalone bundle does not contain required QSB shaders" >&2
  exit 2
}
[[ -d "$STANDALONE_DIR/maps/tiles" ]] || {
  echo "error: standalone bundle does not contain maps/tiles"
  exit 2
}
find "$STANDALONE_DIR/maps/tiles/extension/bin" -type f -name 'osmand_render_helper*' -print -quit 2>/dev/null | grep -q . || {
  echo "error: standalone bundle does not contain the native map helper" >&2
  exit 2
}
find "$STANDALONE_DIR" -type f -path '*/platforms/libqxcb.so' -print -quit | grep -q . || {
  echo "error: standalone bundle does not contain the Qt XCB platform plugin" >&2
  exit 2
}

OUTPUT_PATH="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)/$(basename "$OUTPUT_PATH")"
APPDIR="$(dirname "$OUTPUT_PATH")/iPhotron.AppDir"
[[ ! -e "$APPDIR" ]] || { echo "error: refusing to overwrite existing $APPDIR" >&2; exit 2; }
mkdir -p "$APPDIR/usr/bin"
cp -a "$STANDALONE_DIR/." "$APPDIR/usr/bin/"
install -m 0755 "$ROOT_DIR/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m 0644 "$ROOT_DIR/packaging/appimage/iphoto.desktop" "$APPDIR/iphoto.desktop"
install -m 0644 "$ICON_PATH" "$APPDIR/iphoto.png"

ARCH="${ARCH:-$(uname -m)}" "$APPIMAGETOOL" "$APPDIR" "$OUTPUT_PATH"
echo "Created $OUTPUT_PATH"
