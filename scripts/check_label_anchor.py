"""诊断线标落位：是否统一贴在「模块右出线」的上方、且贴着线。

判定口径（对每条线标）：
  A. 上方贴线：存在某条水平段，线标基线上方 1~6px 内（segY - cy ∈ [1,6]），
     且线标水平范围与该段有交集 → 记为 ABOVE_HUG
  B. 下方：segY - cy 为负且 |·| <= 10 → BELOW
  C. 垂直段旁：贴在某条垂直段左右 8px 内 → SIDE_V
  D. 其它 → OTHER

另外统计：线标起点 x 与其所贴水平段左端的距离（越接近 0 表示越靠近出线端）。
"""
from __future__ import annotations
import sys
import os
import re
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NS = "{http://www.w3.org/2000/svg}"


def _f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def parse_pts(d):
    return [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+),(-?[\d.]+)", d)]


def analyse(svg_text):
    root = ET.fromstring(svg_text)
    segs_h, segs_v = [], []
    for el in root.iter(NS + "polyline"):
        pts = parse_pts(el.get("points", ""))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(y2 - y1) < 0.5 and abs(x2 - x1) >= 0.5:
                segs_h.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
            elif abs(x2 - x1) < 0.5 and abs(y2 - y1) >= 0.5:
                segs_v.append((min(y1, y2), max(y1, y2), (x1 + x2) / 2))

    stat = {"ABOVE_HUG": 0, "BELOW": 0, "SIDE_V": 0, "OTHER": 0}
    offs = []
    for el in root.iter(NS + "text"):
        if el.get("data-layer") != "WIRE_LABELS":
            continue
        x, y = _f(el.get("x")), _f(el.get("y"))
        size = _f(el.get("font-size"), 7)
        txt = (el.text or "")
        w = len(txt) * size * 0.60
        x0 = x if el.get("text-anchor", "start") == "start" else (
            x - w if el.get("text-anchor") == "end" else x - w / 2)
        x1 = x0 + w
        # 一条线标可能同时落在多条线段的范围内，按优先级取最优：上方 > 下方
        kind = "OTHER"
        best = 9
        best_off = None
        for sx0, sx1, sy in segs_h:
            if x1 < sx0 - 2 or x0 > sx1 + 2:
                continue
            d = sy - y
            if 1.0 <= d <= 6.0:
                pr, k = 0, "ABOVE_HUG"
            elif -10.0 <= d < 1.0:
                pr, k = 1, "BELOW"
            else:
                continue
            if pr < best:
                best, kind, best_off = pr, k, x0 - sx0
        if kind == "ABOVE_HUG" and best_off is not None:
            offs.append(best_off)
        if kind == "OTHER":
            best_off = None
        if kind == "OTHER":
            for sy0, sy1, sxx in segs_v:
                if sy0 - 3 <= y <= sy1 + 3 and abs(x - sxx) <= 10:
                    kind = "SIDE_V"
                    break
        stat[kind] += 1
    return stat, offs


SAMPLE_BOM = (
    "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
    "SOURCE,,,,会议话筒,4,,,,\n"
    "WIRELESS_MIC,,,,无线话筒,2,,,,\n"
    "WIRELESS_RX,Shure,ULXD4D,无线接收机,1,dante;control,channels=2,,,\n"
    "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n"
    "PROCESSOR,BSS,BLU-806,音频处理器,1,dante;control,inputs=8;outputs=8,,system,\n"
    "AMP,Powersoft,Quattrocanali 4804,功放,1,dante;control;analog,channels=4,,,\n"
    "SPEAKER,L-Acoustics,KARA,主扩,4,,impedance_ohm=8;power_w=400,,,\n"
    "SWITCH,Cisco,CBS350-8T,Dante 交换机,1,dante,,,,\n"
)


def build_svg(bom_text: str, redundancy="NONE", anon=False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from avcad.workflow.run import run_workflow
    from avcad.render.draw import draw_devices, draw_wires, draw_ports
    from avcad.render.primitives import Canvas
    from avcad.render.svg_render import render_svg
    res = run_workflow(bom_text=bom_text, name="线标落位诊断", redundancy=redundancy)
    proj = res["project"]
    c = Canvas()
    draw_devices(c, proj, anon=anon)
    draw_wires(c, proj, label_all=True)
    draw_ports(c, proj)
    return render_svg(c)


if __name__ == "__main__":
    cases = [("内置样例", SAMPLE_BOM, "NONE")]
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path and os.path.exists(path):
        import base64
        from avcad.ui.app import _decode_upload
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        _e, csv_text, _d, _n = _decode_upload({"filename": os.path.basename(path), "b64": b64})
        cases.insert(0, (os.path.basename(path), csv_text, "NONE"))
    allok = True
    for name, bom, red in cases:
        svg = build_svg(bom, red)
        stat, offs = analyse(svg)
        total = sum(stat.values())
        above = stat["ABOVE_HUG"]
        pct = above * 100.0 / total if total else 0
        avg_off = sum(offs) / len(offs) if offs else 0
        print(f"[{name}] 线标 {total} 条")
        print(f"   贴右出线上方(ABOVE_HUG) {above}  ({pct:.0f}%)")
        print(f"   线下方(BELOW) {stat['BELOW']}   垂直段旁(SIDE_V) {stat['SIDE_V']}   其它(OTHER) {stat['OTHER']}")
        print(f"   距出线端起点平均偏移 {avg_off:.1f}px")
        if pct < 80:
            allok = False
    print("RESULT:", "PASS ✅" if allok else "FAIL ❌")
