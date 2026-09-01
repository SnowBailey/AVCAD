#!/bin/bash
# 打包 AVCAD macOS 应用（.app）。
# 用法: bash packaging/build_app.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="${PY:-/Users/mac/.workbuddy/binaries/python/envs/avcad/bin/python}"

echo "▶ 生成图标（如不存在）"
if [[ ! -f packaging/AVCAD.icns ]]; then
  "$PY" packaging/make_icon.py
fi

echo "▶ PyInstaller 打包"
rm -rf build dist/AVCAD.app
"$PY" -m PyInstaller \
  --noconfirm --windowed \
  --name AVCAD \
  --icon packaging/AVCAD.icns \
  --osx-bundle-identifier com.ezpro.avcad \
  --paths "$ROOT" \
  --add-data "$ROOT/avcad/ui/static:avcad/ui/static" \
  --add-data "$ROOT/avcad/data:avcad/data" \
  --add-data "$ROOT/avcad/config:avcad/config" \
  --hidden-import scripts.check_overlap \
  --hidden-import openpyxl \
  --hidden-import xlrd \
  --hidden-import yaml \
  --hidden-import ezdxf \
  --collect-submodules encodings \
  --exclude-module pytest \
  packaging/avcad_app.py

echo "▶ 附加签名（ad-hoc）"
codesign --force --deep --sign - dist/AVCAD.app || echo "（签名失败，可忽略）"

echo "✅ 完成: dist/AVCAD.app"
du -sh dist/AVCAD.app
