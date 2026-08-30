"""DXF 导出（ezdxf, R2010）。真彩色 + 标准线型 + 图层；中文字体走 simsun 样式。"""
from __future__ import annotations
import ezdxf
from ezdxf.lldxf.const import (
    MTEXT_BOTTOM_LEFT,
    MTEXT_BOTTOM_CENTER,
    MTEXT_BOTTOM_RIGHT,
)
from avcad.render.primitives import Rect, Line, Polyline, Text, Circle, Port

LAYERS = [
    "DEVICES", "LABELS", "PORTS", "WIRES_ANALOG", "WIRES_DIGITAL",
    "WIRES_DANTE", "WIRES_CONTROL", "WIRES_RF", "WIRES_SPEAKER", "WIRES_POWER",
    "WIRE_LABELS", "LEGEND",
]
LAYER_COLOR = {  # ACI
    "DEVICES": 7, "LABELS": 7, "PORTS": 6, "WIRES_ANALOG": 4,
    "WIRES_DIGITAL": 4, "WIRES_DANTE": 5, "WIRES_CONTROL": 6,
    "WIRES_RF": 40, "WIRES_SPEAKER": 1, "WIRES_POWER": 8,
    "WIRE_LABELS": 7, "LEGEND": 7,
}


def _tc(hexcolor):
    h = hexcolor.lstrip("#")
    if len(h) == 6:
        return int(h, 16)
    return 0xFFFFFF


def _y(y):
    return -y  # 屏幕坐标 y 向下 -> DXF y 向上


def render_dxf(canvas, path: str, project_name: str = "AV System"):
    doc = ezdxf.new("R2010")
    for name in LAYERS:
        doc.layers.add(name, color=LAYER_COLOR.get(name, 7))
    for lt in ("DASHED", "DOTTED"):
        if lt not in doc.linetypes:
            try:
                doc.linetypes.add(lt)
            except Exception:
                pass
    if "CJK" not in doc.styles:
        doc.styles.add("CJK", font="simsun.ttc")
    msp = doc.modelspace()

    # 标题
    title = msp.add_mtext(project_name, dxfattribs={"layer": "LABELS", "style": "CJK",
                          "char_height": 12, "true_color": _tc("#f1efe8"), "width": 0})
    title.set_location(insert=(canvas.bounds()[0], _y(canvas.bounds()[3] + 16), 0),
                         attachment_point=MTEXT_BOTTOM_LEFT, rotation=0)

    for p in canvas.primitives:
        if isinstance(p, Rect):
            pts = [(p.x, _y(p.y)), (p.x + p.w, _y(p.y)), (p.x + p.w, _y(p.y + p.h)),
                   (p.x, _y(p.y + p.h)), (p.x, _y(p.y))]
            msp.add_lwpolyline(pts, dxfattribs={"layer": p.layer, "color": 7,
                              "true_color": _tc(p.color), "lineweight": 25})
        elif isinstance(p, Line):
            msp.add_line((p.x1, _y(p.y1)), (p.x2, _y(p.y2)),
                         dxfattribs={"layer": p.layer, "true_color": _tc(p.color),
                                     "linetype": p.ltype.upper() if p.ltype != "solid" else "CONTINUOUS"})
        elif isinstance(p, Polyline):
            pts = [(x, _y(y)) for x, y in p.points]
            msp.add_lwpolyline(pts, dxfattribs={"layer": p.layer, "true_color": _tc(p.color),
                              "linetype": p.ltype.upper() if p.ltype != "solid" else "CONTINUOUS"})
        elif isinstance(p, (Circle, Port)):
            msp.add_circle((p.x, _y(p.y)), p.r,
                           dxfattribs={"layer": p.layer, "true_color": _tc(p.color)})
        elif isinstance(p, Text):
            # 用 MTEXT 替代 TEXT：AutoCAD/BricsCAD 对 MTEXT attachment_point
            # 居中的兼容性远好于单行 TEXT 的 halign/valign 组合，且单行
            # 标题/端口标签不会被旧版 AutoCAD 的 TEXT 对齐 bug 偏移。
            ap_map = {
                "start": MTEXT_BOTTOM_LEFT,
                "middle": MTEXT_BOTTOM_CENTER,
                "end": MTEXT_BOTTOM_RIGHT,
            }
            ap = ap_map.get(p.anchor or "start", MTEXT_BOTTOM_LEFT)
            mt = msp.add_mtext(p.text, dxfattribs={"layer": p.layer, "style": "CJK",
                            "char_height": p.size, "true_color": _tc(p.color), "width": 0})
            mt.set_location(insert=(p.x, _y(p.y), 0), attachment_point=ap, rotation=0)
    doc.saveas(path)
    return doc
