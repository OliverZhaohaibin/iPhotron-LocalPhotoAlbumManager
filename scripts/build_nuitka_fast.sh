#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

QT_PLUGIN_FAMILIES="qml,multimedia,platforms"
LTO_MODE="yes"
NUITKA_MODE_ARGS=(--standalone)
if [[ "$(uname -s)" == "Darwin" ]]; then
  # PyObjC's Foundation bridge requires a real macOS bundle.  ``app-dist``
  # keeps the inspectable standalone layout while satisfying that runtime
  # contract; the benchmark runner resolves the executable inside the bundle.
  NUITKA_MODE_ARGS=(--mode=app-dist --macos-app-mode=gui)
fi
PACKAGE_ARGS=(
  --include-package=iPhoto
  --include-package=maps
  --include-package=OpenGL
  --include-package=OpenGL_accelerate
)
FEATURE_DATA_ARGS=(
  --include-data-dir=src/extension/models=extension/models
  --include-data-dir=src/iPhoto/resources/i18n=iPhoto/resources/i18n
  --include-data-file=src/iPhoto/pets/model_manifest.json=iPhoto/pets/model_manifest.json
  --include-data-dir=src/maps/tiles=maps/tiles
  --include-data-file=src/maps/style.json=maps/style.json
  --include-data-dir=src/maps/map_widget/qml=maps/map_widget/qml
)
if [[ "$(uname -s)" == "Linux" ]]; then
  QT_PLUGIN_FAMILIES=",${QT_PLUGIN_FAMILIES},xcbglintegrations"
  QT_PLUGIN_FAMILIES="${QT_PLUGIN_FAMILIES#,}"
fi

OPTIONAL_RUNTIME_ARGS=(
  --include-package=insightface
  --include-package=onnxruntime
)
if [[ "${IPHOTO_NUITKA_DETAIL_BENCHMARK:-0}" == "1" ]]; then
  QT_PLUGIN_FAMILIES="multimedia,platforms"
  # The packaged Detail benchmark follows the real application entry point and
  # therefore still compiles the production Gallery -> Detail -> QRhi/Metal
  # call graph. Avoid force-including unrelated feature trees: besides making
  # the build much slower, their thousands of native files can exceed macOS'
  # argv limit when Nuitka applies its ad-hoc signature.
  LTO_MODE="no"
  PACKAGE_ARGS=(
    --include-package=iPhoto.resources
  )
  FEATURE_DATA_ARGS=(
    --include-data-dir=src/iPhoto/resources/i18n=iPhoto/resources/i18n
    --include-data-dir=src/iPhoto/gui/ui/icon=iPhoto/gui/ui/icon
    --include-data-dir=src/iPhoto/schemas=iPhoto/schemas
    --include-data-file=src/iPhoto/pets/model_manifest.json=iPhoto/pets/model_manifest.json
  )
  OPTIONAL_RUNTIME_ARGS=(
    --nofollow-import-to=insightface
    --nofollow-import-to=onnxruntime
    --nofollow-import-to=torch
  )
fi

PYTHON_EXECUTABLE="${PYTHON_EXECUTABLE:-python}"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_EXECUTABLE="$ROOT_DIR/.venv/bin/python"
fi

"$PYTHON_EXECUTABLE" -m nuitka \
  "${NUITKA_MODE_ARGS[@]}" \
  --python-flag=no_site \
  --lto="$LTO_MODE" \
  --clang \
  --enable-plugin=pyside6 \
  --include-qt-plugins="$QT_PLUGIN_FAMILIES" \
  --follow-imports \
  --nofollow-import-to=numba \
  --nofollow-import-to=llvmlite \
  --nofollow-import-to=albumentations \
  --nofollow-import-to=albucore \
  --nofollow-import-to=pydantic \
  --nofollow-import-to=pydantic_core \
  --nofollow-import-to=typing_inspection \
  --nofollow-import-to=pytest \
  --nofollow-import-to=iPhoto.tests \
  ${PACKAGE_ARGS[@]+"${PACKAGE_ARGS[@]}"} \
  "${OPTIONAL_RUNTIME_ARGS[@]}" \
  "${FEATURE_DATA_ARGS[@]}" \
  --include-data-file=src/iPhoto/gui/ui/widgets/gl_image_viewer.frag=iPhoto/gui/ui/widgets/gl_image_viewer.frag \
  --include-data-file=src/iPhoto/gui/ui/widgets/gl_image_viewer.vert=iPhoto/gui/ui/widgets/gl_image_viewer.vert \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_rhi.frag=iPhoto/gui/ui/widgets/image_viewer_rhi.frag \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb=iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_rhi.vert=iPhoto/gui/ui/widgets/image_viewer_rhi.vert \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb=iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_overlay.frag=iPhoto/gui/ui/widgets/image_viewer_overlay.frag \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb=iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_overlay.vert=iPhoto/gui/ui/widgets/image_viewer_overlay.vert \
  --include-data-file=src/iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb=iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb \
  --include-data-file=src/iPhoto/gui/ui/widgets/video_renderer.frag=iPhoto/gui/ui/widgets/video_renderer.frag \
  --include-data-file=src/iPhoto/gui/ui/widgets/video_renderer.frag.qsb=iPhoto/gui/ui/widgets/video_renderer.frag.qsb \
  --include-data-file=src/iPhoto/gui/ui/widgets/video_renderer.vert=iPhoto/gui/ui/widgets/video_renderer.vert \
  --include-data-file=src/iPhoto/gui/ui/widgets/video_renderer.vert.qsb=iPhoto/gui/ui/widgets/video_renderer.vert.qsb \
  --assume-yes-for-downloads \
  --output-dir=dist \
  src/iPhoto/gui/main.py
