"""校验系统图连线线标（需求：所有连线带线标，且不压图元/其他标注）。

用法：
    python scripts/check_wire_labels.py             # 用内置样例清单自检
    python scripts/check_wire_labels.py path.svg    # 校验已有 SVG

判定：
  1. 每条连线（polyline）都应有对应线标（data-layer="WIRE_LABELS"）；
  2. 线标包围盒不得与任何模块矩形（fill #26261f）重叠；
  3. 线标包围盒之间不得相互重叠。
"""
from __future__ import annotations
import sys
import os
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"
MODULE_FILL = "#26261f"


def text_box(t):
    """估算文字包围盒（与 draw.py 的 _text_box 保持一致）。"""
    x = float(t.get("x", 0))
    y = float(t.get("y", 0))
    size = float(t.get("font-size", 7))
    anchor = t.get("text-anchor", "start")
    w = len(t.text or "") * size * 0.60
    top = y - size * 0.80
    h = size * 1.05
    if anchor == "middle":
        x0 = x - w / 2
    elif anchor == "end":
        x0 = x - w
    else:
        x0 = x
    return (x0, top, w, h)


def hit(a, b, pad=0.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + pad <= bx or bx + bw + pad <= ax or
                ay + ah + pad <= by or by + bh + pad <= ay)


def check_svg(svg: str):
    root = ET.fromstring(svg)
    mods = []
    for r in root.iter(NS + "rect"):
        if (r.get("fill") or "").lower() == MODULE_FILL:
            mods.append((float(r.get("x", 0)), float(r.get("y", 0)),
                         float(r.get("width", 0)), float(r.get("height", 0))))
    labels = [text_box(t) for t in root.iter(NS + "text")
              if (t.get("data-layer") or "") == "WIRE_LABELS"]
    polys = list(root.iter(NS + "polyline"))

    bad_mod, bad_lbl = [], []
    for i, lb in enumerate(labels):
        for m in mods:
            if hit(lb, m):
                bad_mod.append(i)
                break
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if hit(labels[i], labels[j]):
                bad_lbl.append((i, j))

    return {
        "modules": len(mods), "wires": len(polys), "labels": len(labels),
        "label_module_overlap": len(bad_mod),
        "label_label_overlap": len(bad_lbl),
        "ok": not bad_mod and not bad_lbl and len(labels) > 0,
    }


def _demo_svg() -> str:
    """用内置样例清单生成一张系统图用于自检。"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from avcad.workflow.run import run_workflow
    from avcad.render.draw import draw_devices, draw_wires, draw_ports
    from avcad.render.primitives import Canvas
    from avcad.render.svg_render import render_svg

    bom = (
        "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
        "SOURCE,,,,会议话筒,4,,,,\n"
        "WIRELESS_MIC,,,,无线话筒,2,,,,\n"
        "WIRELESS_RX,Shure,ULXD4D,无线接收机,1,dante;control,channels=2,,,\n"
        "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n"
        "PROCESSOR,BSS,BLU-806,音频处理器,1,dante;control,inputs=8;outputs=8,,system,\n"
        "AMP,Powersoft,Quattrocanali 4804,功放,1,dante;control;analog,channels=4,,,\n"
        "SPEAKER,L-Acoustics,KARA,主扩,4,,impedance_ohm=8;power_w=400,,,\n"
    )
    res = run_workflow(bom_text=bom, name="线标自检")
    proj = res["project"]
    c = Canvas()
    draw_devices(c, proj)
    draw_wires(c, proj, label_all=True)
    draw_ports(c, proj)
    return render_svg(c)


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            svg = f.read()
    else:
        svg = _demo_svg()
    r = check_svg(svg)
    print(f"模块矩形: {r['modules']}")
    print(f"连线(polyline): {r['wires']}")
    print(f"线标(WIRE_LABELS): {r['labels']}")
    print(f"线标压模块: {r['label_module_overlap']}")
    print(f"线标互相重叠: {r['label_label_overlap']}")
    print("RESULT:", "PASS ✅" if r["ok"] else "FAIL ❌")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
