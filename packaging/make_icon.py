"""生成 AVCAD 应用图标 AVCAD.icns（深空底 + 霓虹青→星云紫 渐变环 + AV 字样）。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
ICONSET = OUT / "AVCAD.iconset"
SIZE = 1024

# 图标尺寸表：(像素, 文件名)
SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def make_base() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (5, 7, 13, 255))
    d = ImageDraw.Draw(img)

    # 渐变环（青 → 紫 → 品红），逐像素生成
    cyan = (0, 229, 255)
    purple = (168, 85, 247)
    pink = (244, 114, 182)
    grad = Image.new("RGBA", (SIZE, SIZE))
    px = grad.load()
    outer = (96, 96, SIZE - 96, SIZE - 96)
    cx = cy = SIZE / 2
    for y in range(SIZE):
        for x in range(SIZE):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            # 只在环带内着色
            if not (SIZE * 0.29 <= dist <= SIZE * 0.44):
                continue
            t = ((x + y) / (2 * SIZE)) * 1.6
            if t < 0.5:
                k = t / 0.5
                c = tuple(int(cyan[i] + (purple[i] - cyan[i]) * k) for i in range(3))
            else:
                k = min(1.0, (t - 0.5) / 0.5)
                c = tuple(int(purple[i] + (pink[i] - purple[i]) * k) for i in range(3))
            px[x, y] = (c[0], c[1], c[2], 255)

    # 中心暗底 + 内圈
    d.ellipse((SIZE * 0.10, SIZE * 0.10, SIZE * 0.90, SIZE * 0.90), fill=(10, 14, 26, 255))
    d.ellipse((SIZE * 0.30, SIZE * 0.30, SIZE * 0.70, SIZE * 0.70), fill=(7, 10, 20, 255))
    img = Image.alpha_composite(img, grad)

    # AV 字样
    d = ImageDraw.Draw(img)
    font = None
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, int(SIZE * 0.30))
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    text = "AV"
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1]),
           text, font=font, fill=(234, 242, 255, 255))
    return img


def main() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)

    base = make_base()
    for px, name in SIZES:
        base.resize((px, px), Image.LANCZOS).save(ICONSET / name)

    icns = OUT / "AVCAD.icns"
    if icns.exists():
        icns.unlink()
    subprocess.run(["iconutil", "--convert", "icns", str(ICONSET), "-o", str(icns)],
                   check=True)
    shutil.rmtree(ICONSET)
    print(f"图标已生成: {icns}")

    # Windows 用 .ico（PyInstaller --icon 在 Windows 上必须是 .ico）
    ico = OUT / "AVCAD.ico"
    base.save(ico, format="ICO",
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"图标已生成: {ico}")


if __name__ == "__main__":
    main()
