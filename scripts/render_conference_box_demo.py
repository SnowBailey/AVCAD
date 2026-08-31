"""生成 IPS CF63 会讨天线盒链路示意图，用于人工核对无线会讨拓扑。

场景：1 台 CF6300 主机 + 2 台 CF6300WB 无线会讨天线盒 + 6 只 CF6350 无线单元
      + 4 只 CF6320 有线单元（手拉手）+ 1 台 QU-16 调音台。

预期：
  · 4 只有线单元串成 1 条链 → 主机 CH_1（3 条 CONF T 型线 + 1 条六芯主缆）
  · 天线盒 1 → 主机 BOX 口；天线盒 2 级联到天线盒 1（各 1 条 CONF）
  · 6 只无线单元经 **RF 空中口**分摊到两台天线盒（每台 3 条，不画物理接头）
  · 无线单元不再直连调音台

用法：python3 scripts/render_conference_box_demo.py
输出：docs/system_diagrams/00_样例-会讨天线盒链路.html
"""
from __future__ import annotations
import os
import sys
import json

sys.path.insert(0, ".")

import avcad.ui.app as app  # noqa: E402

OUT_DIR = "docs/system_diagrams"
NAME = "00_样例-会讨天线盒链路"

# 设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气
BOM_ROWS = [
    ["MIC_HOST", "IPS", "CF6300", "有线无线融合会议主机", 1, "", "", "", "", "", ""],
    ["ANTENNA", "IPS", "CF6300WB", "无线会讨天线盒", 2, "", "", "", "", "", ""],
    ["SOURCE", "IPS", "CF6350", "无线数字会议单元", 6, "", "", "", "", "", ""],
    ["SOURCE", "IPS", "CF6320", "会议单元", 4, "", "", "", "", "", ""],
    ["MIXER", "ALLEN&HEATH", "QU-16", "数字调音台", 1, "analog;control",
     json.dumps({"inputs": 16, "outputs": 8}), "", "", "", ""],
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
.note{margin:8px 0;padding:8px 10px;border-radius:8px;font-size:12px;
 background:rgba(251,191,36,.09);border:1px solid rgba(251,191,36,.24);color:#fbbf24}
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

    from avcad.core.build import build_project  # noqa: E402
    proj = build_project(app._entries_from_bom(csv_text), name=NAME)

    def nm(uid):
        i = next((x for x in proj.instances if x.uid == uid), None)
        return f"{i.brand} {i.model}" if i else uid

    conns = [{"from_uid": c.from_uid, "to_uid": c.to_uid,
              "from": f"{nm(c.from_uid)}:{c.from_port.split(':')[-1]}",
              "to": f"{nm(c.to_uid)}:{c.to_port.split(':')[-1]}",
              "signal": c.signal.value, "note": c.note or ""}
             for c in proj.connections]

    rf = [c for c in conns if c["signal"] == "RF"
          and "CF6300WB" in c["to"]]
    conf = [c for c in conns if c["signal"] == "CONF"]
    to_host = [c for c in conns if "CF6300:" in c["to"] and "CF6300WB" not in c["to"]]
    box_to_host = [c for c in to_host if c["from"].startswith("IPS CF6300WB")]

    per_box = {}
    for c in rf:
        per_box[c["to"]] = per_box.get(c["to"], 0) + 1

    ok_rf = (len(rf) == 6 and len(per_box) == 2
             and max(per_box.values()) == 3)
    ok_box = len(box_to_host) == 1 and all("BOX" in c["to"] for c in box_to_host)

    status = [
        f'<span class="badge">设备 {len(proj.instances)}</span>',
        f'<span class="badge">连线 {len(conns)}</span>',
        f'<span class="badge {"ok" if len(rf) == 6 else "bad"}">'
        f'RF 无线链路 {len(rf)} 条（6 只单元期望 6 条）</span>',
        f'<span class="badge {"ok" if ok_rf else "bad"}">'
        f'天线盒分摊 {dict(sorted(per_box.items()))}（期望每台 3 条）</span>',
        f'<span class="badge {"ok" if ok_box else "bad"}">'
        f'天线盒→主机 BOX 口 {len(box_to_host)} 条</span>',
    ]
    if not ok_rf or not ok_box:
        status.append('<span class="badge bad">拓扑异常，见下方明细</span>')

    def table(title, rows, extra_col=False):
        head = ("<tr><th>从</th><th></th><th>到</th><th>信号</th>"
                + ("<th>备注</th>" if extra_col else "") + "</tr>")
        body = "\n".join(
            f'<tr><td>{c["from"]}</td><td>→</td><td>{c["to"]}</td>'
            f'<td>{c["signal"]}</td>'
            + (f'<td>{c["note"]}</td>' if extra_col else "") + "</tr>"
            for c in rows)
        return f"<h2>{title}</h2><table>{head}{body}</table>"

    tabs = [
        table("无线会讨链路（单元 --RF--> 天线盒）", rf, True),
        table("六芯主缆 / 手拉手（CONF）", conf, True),
        table("汇入主机的连线", to_host, True),
    ]
    by_sig = {}
    for c in conns:
        by_sig[c["signal"]] = by_sig.get(c["signal"], 0) + 1
    tabs.append("<h2>连线按信号统计</h2><table>" + "\n".join(
        f'<tr><td>{k}</td><td>{v}</td></tr>'
        for k, v in sorted(by_sig.items())) + "</table>")

    warns = proj.meta.get("wireless_warnings") or []
    if warns:
        tabs.append('<div class="note">告警：' + "；".join(warns) + "</div>")

    html = (TPL.replace("__TITLE__", NAME)
               .replace("__SUB__",
                        "IPS CF63 会讨：无线单元经 RF 空中口进天线盒，"
                        "天线盒用六芯主缆回主机 BOX 口；有线单元手拉手串链进 CH 口")
               .replace("__SVG__", svg)
               .replace("__STATUS__", "".join(status))
               .replace("__TABLE__", "".join(tabs)))

    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, NAME + ".html")
    open(p, "w", encoding="utf-8").write(html)
    print(f"✓ {p}")
    print(f"  设备 {len(proj.instances)} / 连线 {len(conns)} / 按信号 {by_sig}")
    for c in rf:
        print(f"    RF   {c['from']} → {c['to']}")
    for c in conf:
        print(f"    CONF {c['from']} → {c['to']}  [{c['note']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
