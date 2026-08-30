#!/usr/bin/env python3
"""重叠诊断：列出每条穿越模块主体的连线及其颜色、穿越的模块矩形。"""
from __future__ import annotations
import re, sys

path = sys.argv[1]
svg = open(path, encoding="utf-8").read()

rects = []
for m in re.finditer(
    r'<rect x="([\d.\-]+)" y="([\d.\-]+)" width="([\d.\-]+)" height="([\d.\-]+)" fill="#26261f" stroke="([^"]+)"',
    svg,
):
    x, y, w, h, stroke = (m.group(i) for i in (1, 2, 3, 4, 5))
    rects.append((float(x), float(y), float(w), float(h), stroke))

wires = []
for m in re.finditer(r'<polyline points="([^"]+)" fill="none" stroke="([^"]+)"[^>]*?(?:/>|></polyline>)', svg):
    coords = [tuple(map(float, p.split(","))) for p in m.group(1).strip().split()]
    wires.append((coords, m.group(2)))

INSET = 1.0
pid = 0
for coords, color in wires:
    edge = coords[1:-1] if len(coords) > 2 else []
    for (px, py) in edge:
        for (x, y, w, h, stroke) in rects:
            if (x + INSET < px < x + w - INSET) and (y + INSET < py < y + h - INSET):
                pid += 1
                print(f"[overlap #{pid}] color={color} point=({px:.1f},{py:.1f}) inside rect=({x:.0f},{y:.0f},{w:.0f}x{h:.0f}) stroke={stroke}")
                print(f"    wire pts: {[(round(a,1),round(b,1)) for a,b in coords]}")
                break
if pid == 0:
    print("no overlaps")
