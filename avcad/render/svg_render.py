"""SVG 预览渲染（深色主题，信号配色）。"""
from __future__ import annotations
from avcad.render.primitives import Rect, Line, Polyline, Text, Circle, Port

DASH = {"solid": "", "dashed": "6 4", "dotted": "1.5 3"}
FONT = "PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_svg(canvas) -> str:
    minx, miny, maxx, maxy = canvas.bounds()
    pad = 24
    w = max(maxx - minx, 200) + pad * 2
    h = max(maxy - miny, 160) + pad * 2
    ox, oy = minx - pad, miny - pad
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{ox:.0f} {oy:.0f} {w:.0f} {h:.0f}" '
           f'width="100%" font-family="{FONT}" style="background:{canvas.bg}">']
    out.append(f'<rect x="{ox:.0f}" y="{oy:.0f}" width="{w:.0f}" height="{h:.0f}" fill="{canvas.bg}"/>')
    for p in canvas.primitives:
        if isinstance(p, Rect):
            dash = f' stroke-dasharray="{DASH[p.dash]}"' if p.dash else ""
            out.append(f'<rect x="{p.x:.1f}" y="{p.y:.1f}" width="{p.w:.1f}" height="{p.h:.1f}" '
                       f'fill="{p.fill}" stroke="{p.color}" stroke-width="{p.width}"{dash} rx="3"/>')
        elif isinstance(p, Line):
            dash = f' stroke-dasharray="{DASH[p.ltype]}"' if p.ltype != "solid" else ""
            out.append(f'<line x1="{p.x1:.1f}" y1="{p.y1:.1f}" x2="{p.x2:.1f}" y2="{p.y2:.1f}" '
                       f'stroke="{p.color}" stroke-width="{p.width}"{dash}/>')
        elif isinstance(p, Polyline):
            dash = f' stroke-dasharray="{DASH[p.ltype]}"' if p.ltype != "solid" else ""
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in p.points)
            out.append(f'<polyline points="{pts}" fill="none" stroke="{p.color}" '
                       f'stroke-width="{p.width}"{dash} stroke-linejoin="round"/>')
        elif isinstance(p, Circle):
            out.append(f'<circle cx="{p.x:.1f}" cy="{p.y:.1f}" r="{p.r:.1f}" '
                       f'fill="{p.fill}" stroke="{p.color}" stroke-width="{p.width}"/>')
        elif isinstance(p, Port):
            out.append(f'<circle cx="{p.x:.1f}" cy="{p.y:.1f}" r="{p.r:.1f}" fill="{p.color}"/>')
        elif isinstance(p, Text):
            wt = " font-weight=\"bold\"" if p.bold else ""
            # data-layer 仅用于下游校验脚本识别线标，不影响渲染
            out.append(f'<text x="{p.x:.1f}" y="{p.y:.1f}" fill="{p.color}" font-size="{p.size}" '
                       f'text-anchor="{p.anchor}" data-layer="{p.layer}"{wt}>{_esc(p.text)}</text>')
    out.append("</svg>")
    return "\n".join(out)
