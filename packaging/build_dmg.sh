#!/bin/bash
# 把 dist/AVCAD.app 打包成可分发的 DMG 安装盘。
# 用法: bash packaging/build_dmg.sh [版本号]
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="${1:-1.0.0}"
APP="dist/AVCAD.app"
DMG_DIR="dist/dmg_stage"
OUT="dist/AVCAD-${VERSION}-macOS.dmg"

if [[ ! -d "$APP" ]]; then
  echo "❌ 找不到 $APP，请先运行 PyInstaller 打包" >&2
  exit 1
fi

echo "▶ 准备 DMG 舞台目录"
rm -rf "$DMG_DIR"
mkdir -p "$DMG_DIR"
cp -R "$APP" "$DMG_DIR/"
ln -sf /Applications "$DMG_DIR/Applications"

# 使用说明（拖拽安装提示）
cat > "$DMG_DIR/安装说明.txt" <<'TXT'
AVCAD@Bailey@EZPRO · 音视频系统图自动生成

安装方法：
  把左侧的 AVCAD.app 拖到右侧的 Applications（应用程序）文件夹即可。

使用：
  双击 AVCAD.app → 会自动启动本地服务并在浏览器中打开界面。
  关闭窗口即停止服务。

图例库（永久文档）保存位置：
  ~/Library/Application Support/AVCAD/legend_library.json
  首次启动会把内置图例库复制过去，之后的每次修改都落在这里。

若首次打开提示「无法验证开发者」：
  右键点击 AVCAD.app → 选择「打开」→ 再点「打开」即可。
TXT

echo "▶ 清理旧的 DMG"
rm -f "$OUT"

echo "▶ 附加签名（ad-hoc，减少 Gatekeeper 拦截）"
codesign --force --deep --sign - "$APP" || echo "（签名失败，可忽略）"

echo "▶ 生成 DMG（UDZO 压缩）"
hdiutil create -volname "AVCAD ${VERSION}" \
  -srcfolder "$DMG_DIR" \
  -ov -format UDZO -imagekey zlib-level=9 \
  "$OUT"

ls -lh "$OUT"
echo "✅ 完成: $OUT"
