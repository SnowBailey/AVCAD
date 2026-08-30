#!/usr/bin/env python3
"""出图质量自测：扫描 SVG，检测 (1) 连线是否穿过模块主体；(2) 是否存在斜线段。

用法：
    python scripts/check_overlap.py [path/to/system.svg]

判定规则：
- 模块主体 = fill="#26261f" 的 <rect>。
- 连线 = <polyline>（采样除首末点外的内部点，若严格落在某模块矩形内 → 重叠 bug）。
- 斜线 = polyline 中同时存在 dx>0 与 dy>0 的线段（非横平竖直）。

预期：overlap=0, diagonal=0。

`check_svg(svg_text)` 可供后端 /api/validate 直接调用。
"""
from __future__ import annotations
import re, sys, os

DEFAULT = os.path.join(os.path.dirname(__file__), "..", "deliverables",
                       "catalog_samples", "system.svg")


def check_svg(svg: str) -> tuple:
    """返回 (overlap:int, diagonal:int, ok:bool)。"""
    rects = []
    for m in re.finditer(
        r'<rect x="([\d.\-]+)" y="([\d.\-]+)" width="([\d.\-]+)" height="([\d.\-]+)" fill="#26261f" stroke="([^"]+)"',
        svg,
    ):
        x, y, w, h = (float(m.group(i)) for i in (1, 2, 3, 4))
        rects.append((x, y, w, h))

    wires = []
    for m in re.finditer(
        r'<polyline points="([^"]+)" fill="none" stroke="([^"]+)"[^>]*?/>', svg
    ):
        coords = []
        for p in m.group(1).strip().split():
            a, b = p.split(",")
            coords.append((float(a), float(b)))
        wires.append((coords, m.group(2)))

    INSET = 1.0
    hits = 0
    for coords, color in wires:
        edge = coords[1:-1] if len(coords) > 2 else []
        for (px, py) in edge:
            for r in rects:
                x, y, w, h = r[0], r[1], r[2], r[3]
                if (x + INSET < px < x + w - INSET) and (y + INSET < py < y + h - INSET):
                    hits += 1
                    break

    diag = 0
    for coords, color in wires:
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            if abs(x1 - x2) > 1e-6 and abs(y1 - y2) > 1e-6:
                diag += 1

    return hits, diag, (hits == 0 and diag == 0)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    svg = open(path, encoding="utf-8").read()
    overlap, diagonal, ok = check_svg(svg)
    print(f"module rects (fill #26261f): {len(re.findall(r'fill=\"#26261f\"', svg))}")
    print(f"wires (polylines): {len(re.findall(r'<polyline', svg))}")
    print(f"overlap bugs (wire inside module body): {overlap}")
    print(f"diagonal (non-orthogonal) segments: {diagonal}")
    print("RESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
