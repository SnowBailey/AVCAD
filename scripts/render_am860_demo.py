"""生成 IPS AM860 级联示意图，用于人工核对级联逻辑。

场景：20 只会议话筒 + 4 台 AM860（8 进 1 出，LINK 级联）+ 1 台四通道功放 + 4 只吸顶音箱。
预期：4 台 AM860 用 LINK 串成链（3 条 LINK 线），话筒分布到各台 IN，
      **只有主机（第一台）的 OUT 接到功放**。

用法：python3 scripts/render_am860_demo.py
输出：docs/system_diagrams/00_样例-AM860级联示意.html
"""
from __future__ import annotations
import os
import sys
import json

sys.path.insert(0, ".")

import avcad.ui.app as app  # noqa: E402

OUT_DIR = "docs/system_diagrams"
NAME = "00_样例-AM860级联示意"

# 设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气
BOM_ROWS = [
    ["SOURCE", "IPS", "MT110", "会议话筒", 20, "", "", "", "", "", ""],
    ["MIXER", "IPS", "AM860", "智能自动混音器", 4, "analog;phantom",
     json.dumps({"inputs": 8, "outputs": 1, "cascade": 1}), "", "", "", ""],
    ["AMP", "IPS", "DA250Q", "四通道D类功率放大器", 1, "analog;control",
     json.dumps({"channels": 4}), "", "", "", ""],
    ["SPEAKER", "IPS", "CI600", "6.5\"同轴吸顶扬声器", 4, "",
     json.dumps({"impedance_ohm": 8, "power_w": 80}), "", "", "", ""],
]

HEADER = "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气"

TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#03050b;color:#e6eaf2;
 font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
header{padding:18px 26px;border-bottom:1px solid rgba(0,229,255,.18);
 background:radial-gradient(120% 160% at 12% 0%,rgba(0,229,255,.12),transparent 60%)}
h1{margin:0;font-size:19px;letter-spacing:.04em}
h1 span{background:linear-gradient(90deg,#00e5ff,#a855f7);-webkit-background-clip:text;
 -webkit-text-fill-color:transparent}
.sub{margin-top:6px;font-size:12px;color:#7c8aa5}
.canvas{padding:16px;background:
 radial-gradient(70% 55% at 50% 8%,rgba(168,85,247,.10),transparent 70%)}
.canvas svg{width:100%;height:auto;display:block}
.side{padding:16px 26px 40px;border-top:1px solid rgba(0,229,255,.14)}
h2{font-size:13px;color:#00e5ff;margin:18px 0 10px;letter-spacing:.06em}
table{border-collapse:collapse;font-size:12px}
td,th{padding:4px 10px;border-bottom:1px solid rgba(255,255,255,.06);text-align:left}
th{color:#7c8aa5;font-weight:500;font-size:11px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
 border:1px solid rgba(0,229,255,.4);color:#00e5ff;margin-right:6px}
.badge.warn{border-color:rgba(251,191,36,.5);color:#fbbf24}
.badge.bad{border-color:rgba(248,113,113,.5);color:#f87171}
</style></head><body>
<header>
  <h1><span>__TITLE__</span></h1>
  <div class="sub">__SUB__</div>
</header>
  <div class="canvas">__SVG__</div>
  <div class="side">
    __STATUS__
    __TABLE__
  </div>
</body></html>"""


def main():
    import csv as _csv
    import io
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER.split(","))
    w.writerows(BOM_ROWS)
    csv_text = buf.getvalue()
    r = app._dispatch("/api/run", json.dumps(
        {"bom": csv_text, "name": NAME, "require_legend": False}))
    if r.get("error"):
        print("✗", r["error"])
        return 1

    svg = r.get("svg") or ""

    # /api/run 只返回 svg 与 devices，连线明细要用 build_project 单独取
    from avcad.core.build import build_project  # noqa: E402
    proj = build_project(app._entries_from_bom(csv_text), name=NAME)
    name_of = {i.uid: f"{i.brand} {i.model} [{i.uid}]" for i in proj.instances}
    insts = proj.instances
    conns = [{"from": f"{name_of.get(c.from_uid, c.from_uid)}:{c.from_port}",
              "to": f"{name_of.get(c.to_uid, c.to_uid)}:{c.to_port}",
              "signal": c.signal.value, "note": c.note or ""}
             for c in proj.connections]

    by_sig = {}
    for c in conns:
        by_sig[c["signal"]] = by_sig.get(c["signal"], 0) + 1
    link = [c for c in conns if c["signal"] == "LINK"]

    status = [
        f'<span class="badge">设备 {len(insts)}</span>',
        f'<span class="badge">连线 {len(conns)}</span>',
        f'<span class="badge {"ok" if len(link) == 3 else "bad"}">LINK 级联线 {len(link)} 条（4 台期望 3 条）</span>',
    ]
    rows = "\n".join(
        f'<tr><td>{c["from"]}</td><td>→</td><td>{c["to"]}</td>'
        f'<td>{c["signal"]}</td><td>{c["note"]}</td></tr>'
        for c in link)
    table = ("<h2>LINK 级联线明细</h2><table>"
             "<tr><th>从</th><th></th><th>到</th><th>信号</th><th>备注</th></tr>"
             + rows + "</table>")

    # 主机 OUT 是否唯一送后级
    mix_outs = [c for c in conns
                if "AM860" in str(c["from"]) and c["signal"] != "LINK"]
    table += ("<h2>AM860 音频输出</h2><table>"
              "<tr><th>从</th><th>→</th><th>到</th><th>信号</th></tr>"
              + "\n".join(f'<tr><td>{c["from"]}</td><td>→</td>'
                          f'<td>{c["to"]}</td><td>{c["signal"]}</td></tr>'
                          for c in mix_outs)
              + "</table>")
    table += "<h2>连线按信号统计</h2><table>" + "\n".join(
        f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in sorted(by_sig.items())
    ) + "</table>"

    html = (TPL.replace("__TITLE__", NAME)
               .replace("__SUB__", "IPS AM860 智能自动混音器级联验证：4 台级联 = 32 路话筒输入，"
                                   "仅主机 OUT 送后级功放")
               .replace("__SVG__", svg)
               .replace("__STATUS__", "".join(status))
               .replace("__TABLE__", table))

    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, NAME + ".html")
    open(p, "w", encoding="utf-8").write(html)
    print(f"✓ {p}")
    print(f"  设备 {len(insts)} / 连线 {len(conns)} / LINK {len(link)} 条")
    print(f"  按信号：{by_sig}")
    for c in link:
        print(f"    LINK {c['from']} → {c['to']}")
    for c in mix_outs:
        print(f"    OUT  {c['from']} → {c['to']} [{c['signal']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
