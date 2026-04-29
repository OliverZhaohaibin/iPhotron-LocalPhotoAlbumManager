#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/build_nuitka_macos.sh [options]

Build the macOS Nuitka app bundle for iPhotron.

Options:
  --python PATH                 Python executable to use.
  --output-dir DIR              Nuitka output directory. Defaults to dist.
  --jobs N                      Parallel Nuitka jobs. Defaults to half the CPU count.
  --sdk-root DIR                PySide6-OsmAnd-SDK checkout. Defaults to ../PySide6-OsmAnd-SDK.
  --qt-root DIR                 Qt root passed to the SDK build. Defaults to /opt/homebrew/opt/qt.
  --icon PATH                   Optional .icns file for the app bundle.
  --skip-aot                    Skip Numba AOT filter compilation.
  --skip-sdk-runtime-build      Do not run the SDK macOS native runtime build.
  --skip-map-runtime-sync       Do not run scripts/sync_macos_map_extension.py.
  --skip-dependency-fix         Pass --skip-dependency-fix to the macOS runtime sync script.
  -h, --help                    Show this help.

Examples:
  bash scripts/build_nuitka_macos.sh
  bash scripts/build_nuitka_macos.sh --sdk-root ../PySide6-OsmAnd-SDK --output-dir build
  bash scripts/build_nuitka_macos.sh --skip-map-runtime-sync
USAGE
}

die() {
  echo "error: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

require_path() {
  local path="$1"
  [[ -e "$path" ]] || die "required path does not exist: $path"
}

require_command_or_path() {
  local value="$1"
  if [[ "$value" == */* ]]; then
    require_path "$value"
    return
  fi
  command -v "$value" >/dev/null 2>&1 || die "required command not found: $value"
}

require_macos_runtime_staged() {
  local bin_dir="$1"
  require_path "$bin_dir/osmand_render_helper"
  require_path "$bin_dir/osmand_native_widget.dylib"
}

find_built_app_bundle() {
  local preferred_app="$OUTPUT_DIR/main.app"

  if [[ -d "$preferred_app" ]]; then
    printf '%s\n' "$preferred_app"
    return
  fi

  local app_bundle
  app_bundle="$(find "$OUTPUT_DIR" -maxdepth 3 -type d -name "*.app" -print -quit)"
  [[ -n "$app_bundle" ]] || die "Nuitka did not produce a .app bundle under $OUTPUT_DIR"
  printf '%s\n' "$app_bundle"
}

stage_map_tiles_into_app() {
  local app_bundle="$1"
  local app_macos_dir="$app_bundle/Contents/MacOS"
  local app_maps_dir="$app_macos_dir/maps"
  local staged_tiles_dir="$app_maps_dir/tiles"

  require_path "$app_macos_dir"

  echo "Staging map tiles into $staged_tiles_dir..."
  mkdir -p "$app_maps_dir"
  rm -rf "$staged_tiles_dir"
  cp -R "$ROOT_DIR/src/maps/tiles" "$app_maps_dir/"
}

resign_staged_map_runtime() {
  local app_bundle="$1"
  local map_bin_dir="$app_bundle/Contents/MacOS/maps/tiles/extension/bin"

  require_command_or_path "/usr/bin/codesign"
  require_command_or_path "/usr/bin/file"

  if [[ ! -d "$map_bin_dir" ]]; then
    warn "map runtime bin directory not found; skipping map runtime re-sign: $map_bin_dir"
    return
  fi

  echo "Re-signing staged map runtime Mach-O files..."
  while IFS= read -r -d '' binary; do
    if /usr/bin/file "$binary" | grep -q "Mach-O"; then
      /usr/bin/codesign --force --sign - "$binary" >/dev/null
    fi
  done < <(find "$map_bin_dir" -type f -print0)
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON:-}"
OUTPUT_DIR="${OUTPUT_DIR:-dist}"
DEFAULT_JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
if ! [[ "$DEFAULT_JOBS" =~ ^[0-9]+$ ]] || [[ "$DEFAULT_JOBS" -lt 1 ]]; then
  DEFAULT_JOBS=4
fi
DEFAULT_JOBS=$(( (DEFAULT_JOBS + 1) / 2 ))
JOBS="${JOBS:-$DEFAULT_JOBS}"
SDK_ROOT="${SDK_ROOT:-$ROOT_DIR/../PySide6-OsmAnd-SDK}"
QT_ROOT="${QT_ROOT:-/opt/homebrew/opt/qt}"
ICON_PATH="${ICON_PATH:-}"
RUN_AOT=1
RUN_SDK_RUNTIME_BUILD=1
RUN_MAP_RUNTIME_SYNC=1
FIX_MAP_DEPENDENCIES=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 ]] || die "--python requires a value"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || die "--output-dir requires a value"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --jobs)
      [[ $# -ge 2 ]] || die "--jobs requires a value"
      JOBS="$2"
      shift 2
      ;;
    --sdk-root)
      [[ $# -ge 2 ]] || die "--sdk-root requires a value"
      SDK_ROOT="$2"
      shift 2
      ;;
    --qt-root)
      [[ $# -ge 2 ]] || die "--qt-root requires a value"
      QT_ROOT="$2"
      shift 2
      ;;
    --icon)
      [[ $# -ge 2 ]] || die "--icon requires a value"
      ICON_PATH="$2"
      shift 2
      ;;
    --skip-aot)
      RUN_AOT=0
      shift
      ;;
    --skip-sdk-runtime-build)
      RUN_SDK_RUNTIME_BUILD=0
      shift
      ;;
    --skip-map-runtime-sync)
      RUN_MAP_RUNTIME_SYNC=0
      shift
      ;;
    --skip-dependency-fix)
      FIX_MAP_DEPENDENCIES=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || die "this script must be run on macOS"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

require_command_or_path "$PYTHON_BIN"
require_path "$ROOT_DIR/src/iPhoto/gui/main.py"
require_path "$ROOT_DIR/src/iPhoto/schemas"
require_path "$ROOT_DIR/src/iPhoto/gui/ui/icon"
require_path "$ROOT_DIR/src/iPhoto/gui/ui/qml"
require_path "$ROOT_DIR/src/maps/tiles"
require_path "$ROOT_DIR/src/maps/style.json"
require_path "$ROOT_DIR/src/maps/map_widget/qml"

SHADER_FILES=(
  "gl_image_viewer.frag"
  "gl_image_viewer.vert"
  "image_viewer_rhi.frag"
  "image_viewer_rhi.frag.qsb"
  "image_viewer_rhi.vert"
  "image_viewer_rhi.vert.qsb"
  "image_viewer_overlay.frag"
  "image_viewer_overlay.frag.qsb"
  "image_viewer_overlay.vert"
  "image_viewer_overlay.vert.qsb"
  "video_renderer.frag"
  "video_renderer.frag.qsb"
  "video_renderer.vert"
  "video_renderer.vert.qsb"
)

for shader_file in "${SHADER_FILES[@]}"; do
  require_path "$ROOT_DIR/src/iPhoto/gui/ui/widgets/$shader_file"
done

if [[ -n "$ICON_PATH" ]]; then
  require_path "$ICON_PATH"
fi

if [[ "$RUN_AOT" -eq 1 ]]; then
  echo "Building AOT filter extension..."
  "$PYTHON_BIN" "$ROOT_DIR/src/iPhoto/core/filters/build_jit.py"
else
  warn "skipping AOT filter build"
fi

if [[ "$RUN_MAP_RUNTIME_SYNC" -eq 1 ]]; then
  SDK_BUILD_SCRIPT="$SDK_ROOT/tools/osmand_render_helper_native/build_macos.sh"
  STAGED_EXTENSION_BIN="$ROOT_DIR/src/maps/tiles/extension/bin"
  SYNC_WITH_SDK=1

  if [[ "$RUN_SDK_RUNTIME_BUILD" -eq 1 ]]; then
    if [[ -f "$SDK_BUILD_SCRIPT" ]]; then
      echo "Building macOS OsmAnd runtime from SDK..."
      QT_ROOT="$QT_ROOT" bash "$SDK_BUILD_SCRIPT"
    else
      warn "SDK build script not found: $SDK_BUILD_SCRIPT"
      require_macos_runtime_staged "$STAGED_EXTENSION_BIN"
      warn "using already staged macOS map runtime under $STAGED_EXTENSION_BIN"
      RUN_SDK_RUNTIME_BUILD=0
      SYNC_WITH_SDK=0
    fi
  fi

  if [[ "$SYNC_WITH_SDK" -eq 1 && -d "$SDK_ROOT" ]]; then
    sync_args=("$ROOT_DIR/scripts/sync_macos_map_extension.py" "--sdk-root" "$SDK_ROOT")
    if [[ "$FIX_MAP_DEPENDENCIES" -eq 0 ]]; then
      sync_args+=("--skip-dependency-fix")
    fi
    echo "Syncing macOS map runtime into src/maps/tiles/extension..."
    "$PYTHON_BIN" "${sync_args[@]}"
  else
    if [[ "$SYNC_WITH_SDK" -eq 0 ]]; then
      warn "skipping SDK sync because the SDK build script was unavailable"
    else
      warn "SDK root not found: $SDK_ROOT"
    fi
    require_macos_runtime_staged "$STAGED_EXTENSION_BIN"
    warn "using already staged macOS map runtime under $STAGED_EXTENSION_BIN"
  fi
else
  warn "skipping macOS map runtime sync"
  require_macos_runtime_staged "$ROOT_DIR/src/maps/tiles/extension/bin"
fi

nuitka_args=(
  "-m" "nuitka"
  "--standalone"
  "--macos-create-app-bundle"
  "--macos-app-name=iPhotron"
  "--macos-app-mode=gui"
  "--output-filename=iPhotron"
  "--jobs=$JOBS"
  "--python-flag=no_site"
  "--lto=yes"
  "--clang"
  "--enable-plugin=pyside6"
  "--include-qt-plugins=qml,multimedia"
  "--follow-imports"
  "--nofollow-import-to=numba"
  "--nofollow-import-to=llvmlite"
  "--nofollow-import-to=albumentations"
  "--nofollow-import-to=albucore"
  "--nofollow-import-to=pydantic"
  "--nofollow-import-to=pydantic_core"
  "--nofollow-import-to=typing_inspection"
  "--nofollow-import-to=iPhoto.tests"
  "--nofollow-import-to=pytest"
  "--include-package=iPhoto"
  "--include-package=maps"
  "--include-package=OpenGL"
  "--include-package=OpenGL_accelerate"
  "--include-package=cv2"
  "--include-package=reverse_geocoder"
  "--include-package=insightface"
  "--include-package=onnxruntime"
  "--include-data-dir=$ROOT_DIR/src/iPhoto/schemas=iPhoto/schemas"
  "--include-data-dir=$ROOT_DIR/src/iPhoto/gui/ui/icon=iPhoto/gui/ui/icon"
  "--include-data-dir=$ROOT_DIR/src/iPhoto/gui/ui/qml=iPhoto/gui/ui/qml"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/gl_image_viewer.frag=iPhoto/gui/ui/widgets/gl_image_viewer.frag"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/gl_image_viewer.vert=iPhoto/gui/ui/widgets/gl_image_viewer.vert"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_rhi.frag=iPhoto/gui/ui/widgets/image_viewer_rhi.frag"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb=iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_rhi.vert=iPhoto/gui/ui/widgets/image_viewer_rhi.vert"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb=iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_overlay.frag=iPhoto/gui/ui/widgets/image_viewer_overlay.frag"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb=iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_overlay.vert=iPhoto/gui/ui/widgets/image_viewer_overlay.vert"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb=iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/video_renderer.frag=iPhoto/gui/ui/widgets/video_renderer.frag"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/video_renderer.frag.qsb=iPhoto/gui/ui/widgets/video_renderer.frag.qsb"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/video_renderer.vert=iPhoto/gui/ui/widgets/video_renderer.vert"
  "--include-data-file=$ROOT_DIR/src/iPhoto/gui/ui/widgets/video_renderer.vert.qsb=iPhoto/gui/ui/widgets/video_renderer.vert.qsb"
  # Keep maps/tiles out of Nuitka's data-file list. Nuitka signs all copied
  # data paths in one codesign call on macOS, and the map resource tree can
  # exceed ARG_MAX. The tree is copied into the app bundle after Nuitka returns.
  "--include-data-file=$ROOT_DIR/src/maps/style.json=maps/style.json"
  "--include-data-dir=$ROOT_DIR/src/maps/map_widget/qml=maps/map_widget/qml"
  "--assume-yes-for-downloads"
  "--output-dir=$OUTPUT_DIR"
)

if [[ -d "$ROOT_DIR/src/extension/models" ]]; then
  nuitka_args+=("--include-data-dir=$ROOT_DIR/src/extension/models=extension/models")
else
  warn "face model cache not found; continuing without bundled extension/models"
fi

if [[ -n "$ICON_PATH" ]]; then
  nuitka_args+=("--macos-app-icon=$ICON_PATH")
fi

nuitka_args+=("$ROOT_DIR/src/iPhoto/gui/main.py")

echo "Building macOS app bundle with Nuitka..."
"$PYTHON_BIN" "${nuitka_args[@]}"

APP_BUNDLE="$(find_built_app_bundle)"
stage_map_tiles_into_app "$APP_BUNDLE"
"$PYTHON_BIN" "$ROOT_DIR/scripts/sync_macos_map_extension.py" --repair-app-bundle "$APP_BUNDLE"
resign_staged_map_runtime "$APP_BUNDLE"

echo "Build complete. App bundles:"
find "$OUTPUT_DIR" -maxdepth 3 -name "*.app" -print
