#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/build_flatpak.sh --standalone-dir DIR --icon PNG --output FILE"
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
STANDALONE_DIR=""
ICON_PATH=""
OUTPUT_PATH=""
FLATPAK_BIN="${FLATPAK_BIN:-flatpak}"
FLATPAK_BUILDER_BIN="${FLATPAK_BUILDER_BIN:-flatpak-builder}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_ID="com.github.OliverZhaohaibin.iPhotron"
RUNTIME_VERSION="25.08"

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

[[ "$(uname -s)" == "Linux" ]] || { echo "error: Flatpak builds require Linux" >&2; exit 2; }
[[ -n "$STANDALONE_DIR" && -d "$STANDALONE_DIR" ]] || { echo "error: invalid --standalone-dir" >&2; exit 2; }
[[ -n "$ICON_PATH" && -f "$ICON_PATH" ]] || { echo "error: --icon must name an existing PNG" >&2; exit 2; }
[[ "$ICON_PATH" == *.png ]] || { echo "error: Flatpak icon must be a PNG" >&2; exit 2; }
[[ -n "$OUTPUT_PATH" ]] || { echo "error: --output is required" >&2; exit 2; }
[[ "$OUTPUT_PATH" == *.flatpak ]] || { echo "error: Flatpak output must use the .flatpak suffix" >&2; exit 2; }
command -v "$FLATPAK_BIN" >/dev/null 2>&1 || { echo "error: flatpak not found" >&2; exit 2; }
command -v "$FLATPAK_BUILDER_BIN" >/dev/null 2>&1 || { echo "error: flatpak-builder not found" >&2; exit 2; }

ENTRYPOINT=""
for candidate in entrypoint.bin entrypoint main.bin main; do
  if [[ -x "$STANDALONE_DIR/$candidate" ]]; then
    ENTRYPOINT="$candidate"
    break
  fi
done
[[ -n "$ENTRYPOINT" ]] || {
  echo "error: standalone entry point entrypoint.bin/entrypoint/main.bin/main not found" >&2
  exit 2
}

find "$STANDALONE_DIR" -type f -name '*.qsb' -print -quit | grep -q . || {
  echo "error: standalone bundle does not contain required QSB shaders" >&2
  exit 2
}
[[ -d "$STANDALONE_DIR/maps/tiles" ]] || {
  echo "error: standalone bundle does not contain maps/tiles" >&2
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

ARCH="$($FLATPAK_BIN --default-arch)"
[[ "$ARCH" == "x86_64" ]] || {
  echo "error: the maintained Flatpak release contract currently supports x86_64 only" >&2
  exit 2
}

OUTPUT_DIR="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)"
OUTPUT_PATH="$OUTPUT_DIR/$(basename "$OUTPUT_PATH")"
[[ ! -e "$OUTPUT_PATH" ]] || { echo "error: refusing to overwrite existing $OUTPUT_PATH" >&2; exit 2; }

VERSION="$($PYTHON_BIN -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/iphotron-flatpak.XXXXXX")"
trap 'rm -rf "$BUILD_ROOT"' EXIT
CONTEXT_DIR="$BUILD_ROOT/context"
mkdir -p "$CONTEXT_DIR/payload"
cp -a "$STANDALONE_DIR/." "$CONTEXT_DIR/payload/"
cp "$ICON_PATH" "$CONTEXT_DIR/iphotron.png"
cp "$ROOT_DIR/packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.yml" "$CONTEXT_DIR/manifest.yml"
cp "$ROOT_DIR/packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.desktop" "$CONTEXT_DIR/"
cp "$ROOT_DIR/packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.metainfo.xml" "$CONTEXT_DIR/"
cp "$ROOT_DIR/packaging/flatpak/iphotron-launcher" "$CONTEXT_DIR/"

"$FLATPAK_BUILDER_BIN" \
  --force-clean \
  --install-deps-from=flathub \
  --default-branch="$VERSION" \
  --repo="$BUILD_ROOT/repo" \
  "$BUILD_ROOT/build" \
  "$CONTEXT_DIR/manifest.yml"

"$FLATPAK_BIN" build-bundle \
  --arch="$ARCH" \
  --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo \
  "$BUILD_ROOT/repo" \
  "$OUTPUT_PATH" \
  "$APP_ID" \
  "$VERSION"

"$PYTHON_BIN" "$ROOT_DIR/tools/build_manifest.py" \
  --root "$ROOT_DIR" \
  --artifact "$OUTPUT_PATH" \
  --build-driver "$ROOT_DIR/scripts/build_flatpak.sh" \
  --build-flag "profile=flatpak-standalone-wrapper" \
  --build-flag "app_id=$APP_ID" \
  --build-flag "runtime=$RUNTIME_VERSION" \
  --build-flag "architecture=$ARCH" \
  --native-runtime "$STANDALONE_DIR/maps/tiles/extension/bin" \
  --asset "$STANDALONE_DIR/maps/tiles" \
  --asset "$STANDALONE_DIR/iPhoto/resources/i18n" \
  --output "${OUTPUT_PATH}.build-manifest.json"

echo "Created $OUTPUT_PATH"
