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
  --low-memory                  Use one compiler job, disable LTO, and enable Nuitka low-memory mode.
  --sdk-root DIR                PySide6-OsmAnd-SDK checkout. Defaults to ../PySide6-OsmAnd-SDK.
  --qt-root DIR                 Qt root passed to the SDK build. Defaults to /opt/homebrew/opt/qt.
  --icon PATH                   App icon (.icns or convertible image). Defaults to docs/picture/logo_new.ico.
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

PYSIDE_ASSETDOWNLOADER_DIR=""
PYSIDE_ASSETDOWNLOADER_BACKUP=""

restore_pyside_assetdownloader() {
  if [[ -n "$PYSIDE_ASSETDOWNLOADER_BACKUP" && -d "$PYSIDE_ASSETDOWNLOADER_BACKUP" ]]; then
    if [[ ! -e "$PYSIDE_ASSETDOWNLOADER_DIR" ]]; then
      mv "$PYSIDE_ASSETDOWNLOADER_BACKUP" "$PYSIDE_ASSETDOWNLOADER_DIR"
    else
      warn "PySide assetdownloader already exists; backup retained at $PYSIDE_ASSETDOWNLOADER_BACKUP"
    fi
  fi
}

hide_unused_pyside_assetdownloader() {
  local pyside_root
  local workaround_dir
  pyside_root="$("$PYTHON_BIN" -c 'from pathlib import Path; import PySide6; print(Path(PySide6.__file__).resolve().parent)')"
  PYSIDE_ASSETDOWNLOADER_DIR="$pyside_root/Qt/qml/Qt/labs/assetdownloader"
  workaround_dir="$OUTPUT_DIR/.build-workarounds"
  PYSIDE_ASSETDOWNLOADER_BACKUP="$workaround_dir/assetdownloader"
  if [[ ! -d "$PYSIDE_ASSETDOWNLOADER_DIR" && -d "$PYSIDE_ASSETDOWNLOADER_BACKUP" ]]; then
    mv "$PYSIDE_ASSETDOWNLOADER_BACKUP" "$PYSIDE_ASSETDOWNLOADER_DIR"
  fi
  if [[ ! -d "$PYSIDE_ASSETDOWNLOADER_DIR" ]]; then
    return
  fi
  mkdir -p "$workaround_dir"
  [[ ! -e "$PYSIDE_ASSETDOWNLOADER_BACKUP" ]] || die \
    "stale PySide assetdownloader backup exists: $PYSIDE_ASSETDOWNLOADER_BACKUP"
  mv "$PYSIDE_ASSETDOWNLOADER_DIR" "$PYSIDE_ASSETDOWNLOADER_BACKUP"
  trap restore_pyside_assetdownloader EXIT
  echo "Temporarily hiding unused PySide Qt.labs.assetdownloader for Nuitka 4.0.x..."
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
  local app_maps_dir="$app_bundle/Contents/Resources/maps"
  local staged_tiles_dir="$app_maps_dir/tiles"
  local archive_path="$app_maps_dir/extension.tar"

  require_path "$app_macos_dir"

  echo "Staging map extension for dependency repair..."
  mkdir -p "$app_maps_dir"
  rm -rf "$staged_tiles_dir"
  rm -f "$archive_path"
  mkdir -p "$staged_tiles_dir"
  cp -R "$ROOT_DIR/src/maps/tiles/extension" "$staged_tiles_dir/"
  # Finder/download provenance can be copied with map resources and is not
  # valid signing metadata inside an app bundle. Clear it before signing the
  # staged native binaries and sealing Resources.
  /usr/bin/xattr -cr "$staged_tiles_dir"
}

resign_staged_map_runtime() {
  local app_bundle="$1"
  local map_bin_dir="$app_bundle/Contents/Resources/maps/tiles/extension/bin"

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

archive_staged_map_extension() {
  local app_bundle="$1"
  local app_maps_dir="$app_bundle/Contents/Resources/maps"
  local staged_tiles_dir="$app_maps_dir/tiles"
  local archive_path="$app_maps_dir/extension.tar"

  require_command_or_path "/usr/bin/tar"
  require_path "$staged_tiles_dir/extension"
  echo "Archiving map extension as one lazily installed resource..."
  COPYFILE_DISABLE=1 /usr/bin/tar -cf "$archive_path" \
    -C "$staged_tiles_dir" extension
  rm -rf "$staged_tiles_dir"
  require_path "$archive_path"
}

prune_packaged_development_files() {
  local app_bundle="$1"
  local torch_headers="$app_bundle/Contents/MacOS/torch/include"

  # PyTorch headers are only used to compile C++ extensions. They add roughly
  # 10k files to the app resource seal and are never read by iPhotron at
  # runtime, so retaining them directly taxes dyld verification and disk use.
  if [[ -d "$torch_headers" ]]; then
    echo "Removing packaged PyTorch development headers..."
    rm -rf "$torch_headers"
  fi
}

resign_app_bundle() {
  local app_bundle="$1"

  require_command_or_path "/usr/bin/codesign"
  echo "Re-signing completed app bundle..."
  # The 45k-file extension is sealed as one archive. It is installed by the
  # existing background map worker on first map use, outside the startup path.
  /usr/bin/codesign --force --sign - "$app_bundle"
  /usr/bin/codesign --verify --strict --verbose=2 "$app_bundle"
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
ICON_PATH="${ICON_PATH:-$ROOT_DIR/docs/picture/logo_new.ico}"
RUN_AOT=1
RUN_SDK_RUNTIME_BUILD=1
RUN_MAP_RUNTIME_SYNC=1
FIX_MAP_DEPENDENCIES=1
LOW_MEMORY=0
LTO_MODE="yes"

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
    --low-memory)
      LOW_MEMORY=1
      shift
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

if [[ "$LOW_MEMORY" -eq 1 ]]; then
  JOBS=1
  LTO_MODE="no"
fi

# Keep compiler caches inside the final selected writable build root. Sandboxed
# CI and local verification runners may not be allowed to mutate ~/Library/Caches.
NUITKA_CACHE_DIR="${NUITKA_CACHE_DIR:-$OUTPUT_DIR/.nuitka-cache}"
export NUITKA_CACHE_DIR

[[ "$(uname -s)" == "Darwin" ]] || die "this script must be run on macOS"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

require_command_or_path "$PYTHON_BIN"
require_path "$ROOT_DIR/src/entrypoint.py"
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

require_path "$ICON_PATH"
if [[ "$ICON_PATH" != *.icns ]]; then
  "$PYTHON_BIN" -c 'import imageio' >/dev/null 2>&1 || die \
    "imageio is required for Nuitka to convert the app icon; install it with: $PYTHON_BIN -m pip install imageio"
fi
"$PYTHON_BIN" -c 'import exiftool, pillow_heif, _pillow_heif' >/dev/null 2>&1 || die \
  "PyExifTool, pillow-heif, and its native extension are required; install them with: $PYTHON_BIN -m pip install pyexiftool pillow-heif"

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
  "--lto=$LTO_MODE"
  "--clang"
  "--enable-plugin=pyside6"
  "--include-qt-plugins=qml,multimedia,platforms"
  "--follow-imports"
  "--nofollow-import-to=numba"
  "--nofollow-import-to=llvmlite"
  "--nofollow-import-to=albumentations"
  "--nofollow-import-to=albucore"
  "--nofollow-import-to=pydantic"
  "--nofollow-import-to=pydantic_core"
  "--nofollow-import-to=typing_inspection"
  "--nofollow-import-to=insightface.thirdparty.face3d"
  "--nofollow-import-to=iPhoto.tests"
  "--nofollow-import-to=pytest"
  "--include-package=iPhoto"
  "--include-package=maps"
  "--include-package=OpenGL"
  "--include-package=OpenGL_accelerate"
  "--include-package=cv2"
  "--include-package=reverse_geocoder"
  "--include-package=insightface"
  "--include-package=exiftool"
  "--include-package=pillow_heif"
  "--include-module=_pillow_heif"
  "--noinclude-data-files=torch/include"
  "--include-data-dir=$ROOT_DIR/src/iPhoto/resources/i18n=iPhoto/resources/i18n"
  "--include-data-file=$ROOT_DIR/src/iPhoto/pets/model_manifest.json=iPhoto/pets/model_manifest.json"
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

if [[ "$LOW_MEMORY" -eq 1 ]]; then
  nuitka_args+=("--low-memory")
fi

if [[ -d "$ROOT_DIR/src/extension/models" ]]; then
  nuitka_args+=("--include-data-dir=$ROOT_DIR/src/extension/models=extension/models")
else
  warn "face model cache not found; continuing without bundled extension/models"
fi

nuitka_args+=("--macos-app-icon=$ICON_PATH")

nuitka_args+=("$ROOT_DIR/src/entrypoint.py")

hide_unused_pyside_assetdownloader
echo "Building macOS app bundle with Nuitka..."
"$PYTHON_BIN" "${nuitka_args[@]}"

APP_BUNDLE="$(find_built_app_bundle)"
stage_map_tiles_into_app "$APP_BUNDLE"
"$PYTHON_BIN" "$ROOT_DIR/scripts/sync_macos_map_extension.py" --repair-app-bundle "$APP_BUNDLE"
resign_staged_map_runtime "$APP_BUNDLE"
archive_staged_map_extension "$APP_BUNDLE"
prune_packaged_development_files "$APP_BUNDLE"
resign_app_bundle "$APP_BUNDLE"
"$PYTHON_BIN" "$ROOT_DIR/tools/build_manifest.py" \
  --root "$ROOT_DIR" \
  --artifact "$APP_BUNDLE/Contents/MacOS/iPhotron" \
  --build-driver "$ROOT_DIR/scripts/build_nuitka_macos.sh" \
  --build-flag "profile=macos" \
  --build-flag "low_memory=$LOW_MEMORY" \
  --native-runtime "$ROOT_DIR/src/maps/tiles/extension/bin" \
  --asset "$ROOT_DIR/src/maps/tiles" \
  --asset "$ROOT_DIR/src/iPhoto/resources/i18n" \
  --output "$OUTPUT_DIR/build-manifest.json"

echo "Build complete. App bundles:"
find "$OUTPUT_DIR" -maxdepth 3 -name "*.app" -print
