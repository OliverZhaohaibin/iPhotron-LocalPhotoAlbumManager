#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python -m nuitka \
  --standalone \
  --python-flag=no_site \
  --lto=yes \
  --clang \
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
  --include-package=insightface \
  --include-package=onnxruntime \
  --include-data-dir=src/extension/models=extension/models \
  --include-data-dir=src/maps/tiles=maps/tiles \
  --include-data-file=src/maps/style.json=maps/style.json \
  --include-data-dir=src/maps/map_widget/qml=maps/map_widget/qml \
  --assume-yes-for-downloads \
  --output-dir=dist \
  src/iPhoto/gui/main.py
