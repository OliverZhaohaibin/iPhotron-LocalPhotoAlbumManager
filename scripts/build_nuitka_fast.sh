#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PROJECT_VERSION="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"

optional_model_args=()
if [[ -d "$ROOT_DIR/src/extension/models" ]]; then
  optional_model_args+=("--include-data-dir=src/extension/models=extension/models")
fi

python -m nuitka \
  --standalone \
  --python-flag=no_site \
  --lto=yes \
  --clang \
  --enable-plugin=pyside6 \
  --include-qt-plugins=qml,multimedia,xcbglintegrations,platforms \
  --follow-imports \
  --nofollow-import-to=numba \
  --nofollow-import-to=llvmlite \
  --nofollow-import-to=albumentations \
  --nofollow-import-to=albucore \
  --nofollow-import-to=pydantic \
  --nofollow-import-to=pydantic_core \
  --nofollow-import-to=typing_inspection \
  --nofollow-import-to=insightface.thirdparty.face3d \
  --nofollow-import-to=pytest \
  --nofollow-import-to=iPhoto.tests \
  --include-package=iPhoto \
  --include-package=maps \
  --include-package=OpenGL \
  --include-package=OpenGL_accelerate \
  --include-package=insightface \
  --include-package=exiftool \
  --include-package=pillow_heif \
  --include-module=_pillow_heif \
  --noinclude-data-files=torch/include \
  "${optional_model_args[@]}" \
  --include-data-dir=src/iPhoto/resources/i18n=iPhoto/resources/i18n \
  --include-data-file=src/iPhoto/pets/model_manifest.json=iPhoto/pets/model_manifest.json \
  --include-data-dir=src/maps/tiles=maps/tiles \
  --include-data-file=src/maps/style.json=maps/style.json \
  --include-data-dir=src/maps/map_widget/qml=maps/map_widget/qml \
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
  src/entrypoint.py

ARTIFACT_PATH=""
for candidate in dist/entrypoint.dist/entrypoint.bin dist/entrypoint.dist/entrypoint; do
  if [[ -f "$candidate" ]]; then
    ARTIFACT_PATH="$candidate"
    break
  fi
done
[[ -n "$ARTIFACT_PATH" ]] || { echo "error: built entrypoint not found" >&2; exit 2; }
python tools/build_manifest.py \
  --root "$ROOT_DIR" \
  --artifact "$ARTIFACT_PATH" \
  --artifact-tree "$ROOT_DIR/dist/entrypoint.dist" \
  --build-driver "$ROOT_DIR/scripts/build_nuitka_fast.sh" \
  --build-flag "profile=fast" \
  --build-flag "project_version=$PROJECT_VERSION" \
  --build-flag "lto=yes" \
  --build-flag "compiler=clang" \
  --native-runtime "$ROOT_DIR/src/maps/tiles/extension/bin" \
  --asset "$ROOT_DIR/src/maps/tiles" \
  --asset "$ROOT_DIR/src/iPhoto/resources/i18n" \
  --output "$ROOT_DIR/dist/build-manifest.json"
