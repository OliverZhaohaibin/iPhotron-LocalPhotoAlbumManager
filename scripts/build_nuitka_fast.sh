#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python -m nuitka \
  --standalone \
  --python-flag=no_site \
  --lto=yes \
  --clang \
  --enable-plugin=pyside6 \
  --include-qt-plugins=qml,multimedia \
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
  --include-package=iPhoto \
  --include-package=maps \
  --include-package=OpenGL \
  --include-package=OpenGL_accelerate \
  --include-package=insightface \
  --include-package=onnxruntime \
  --include-data-dir=src/extension/models=extension/models \
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
  src/iPhoto/gui/main.py
