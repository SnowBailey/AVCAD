"""把三份真实清单渲染成可检查的系统图（HTML + DXF）。

用法：python3 scripts/render_projects.py
输出：docs/system_diagrams/<方案名>.html 与 .dxf
"""
from __future__ import annotations
import os
import sys
import json
import html
import collections

sys.path.insert(0, ".")

from avcad.workflow.importers import build_entries, to_bom_csv  # noqa: E402
from avcad.core.build import build_project  # noqa: E402
import avcad.ui.app as app  # noqa: E402

OUT_DIR = "docs/system_diagrams"

HEZE = "/Users/mac/Desktop/202601/华演出-菏泽曹州古城广场演出系统20260813.xlsx"
YOUTENG = "/Users/mac/Desktop/202601/友腾-EAW音频扩声20260807.xlsx"
TAIYANG = "/Users/mac/Desktop/202601/文博-太阳纸业20260806.xlsx"

# (输出名, 文件路径, sheet, 是否主图)
JOBS = [
    ("01_菏泽曹州古城广场演出系统", HEZE, None, True),
    ("02_友腾-L-ACOUSTICS方案", YOUTENG, "L-ACOUSTICS", False),
    ("03_友腾-EAW1-RSX有源方案", YOUTENG, "EAW1", True),
    ("04_友腾-EAW2-RSX方案", YOUTENG, "EAW2", False),
    ("05_友腾-EAW3-KF210方案", YOUTENG, "EAW3 KF210", False),
    ("06_友腾-EAW4-NT208L方案", YOUTENG, "EAW4", False),
    ("07_太阳纸业-1F会议室", TAIYANG, "1F会议室", False),
    ("08_太阳纸业-2F会议室", TAIYANG, "2F会议室", False),
    ("09_太阳纸业-3F会议室", TAIYANG, "3F会议室", True),
    ("10_太阳纸业-4F会议室", TAIYANG, "4F会议室", False),
]

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
.wrap{display:grid;grid-template-columns:1fr 340px;gap:0;height:calc(100vh - 92px)}
.canvas{overflow:auto;padding:16px;background:
 radial-gradient(70% 55% at 50% 8%,rgba(168,85,247,.10),transparent 70%)}
.canvas svg{width:100%;height:auto;display:block}
.side{border-left:1px solid rgba(0,229,255,.14);overflow:auto;padding:16px 18px}
h2{font-size:13px;color:#00e5ff;margin:0 0 10px;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{padding:4px 6px;border-bottom:1px solid rgba(255,255,255,.06);text-align:left}
th{color:#7c8aa5;font-weight:500;font-size:11px}
td.n{text-align:right;color:#9fb0c8;font-variant-numeric:tabular-nums}
.cat{color:#a855f7;font-size:11px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
 border:1px solid rgba(0,229,255,.4);color:#00e5ff;margin-right:6px}
.badge.warn{border-color:rgba(251,191,36,.5);color:#fbbf24}
.badge.bad{border-color:rgba(248,113,113,.5);color:#f87171}
.note{margin:8px 0;padding:8px 10px;border-radius:8px;font-size:12px;
 background:rgba(251,191,36,.09);border:1px solid rgba(251,191,36,.24);color:#fbbf24}
.ok{background:rgba(52,211,153,.09);border-color:rgba(52,211,153,.24);color:#34d399}
</style></head><body>
<header>
  <h1><span>__TITLE__</span></h1>
  <div class="sub">__SUB__</div>
</header>
<div class="wrap">
  <div class="canvas">__SVG__</div>
  <div class="side">
    <h2>校验状态</h2>
    __STATUS__
    <h2 style="margin-top:18px">设备清单</h2>
    <table><tr><th>类别</th><th>型号</th><th class="n">数量</th></tr>
    __ROWS__
    </table>
    __EXCLUDED__
  </div>
</div>
</body></html>"""


def render(name, path, sheet, is_main):
    entries, dropped = build_entries(path, sheet=sheet)
    r = app._dispatch("/api/run", json.dumps(
        {"bom": to_bom_csv(entries), "name": name, "require_legend": False}))
    if r.get("error"):
        print(f"  ✗ {name}: {r['error']}")
        return None

    proj = build_project(entries, name=name)
    inst = {i.uid: i for i in proj.instances}
    touched = set()
    for c in proj.connections:
        touched.add(c.from_uid)
        touched.add(c.to_uid)
    orphans = [i for uid, i in inst.items()
               if uid not in touched and i.category != "WIRELESS_MIC"]

    v = r.get("validation") or {}
    badges = [
        f'<span class="badge">设备 {len(proj.instances)}</span>',
        f'<span class="badge">连线 {len(proj.connections)}</span>',
        f'<span class="badge">重叠 {v.get("overlap")} / 斜线 {v.get("diagonal")}</span>',
    ]
    notes = []
    if orphans:
        cnt = collections.Counter(i.category for i in orphans)
        detail = "、".join(f"{k}×{n}" for k, n in cnt.most_common(4))
        notes.append(f"孤立设备 {len(orphans)} 台（{detail}）——"
                     f"多为清单未配前端设备或音源超出下游输入路数")
    for w in (proj.meta.get("amp_warnings") or []):
        notes.append(html.escape(w))
    for w in (proj.meta.get("wireless_warnings") or []):
        notes.append(html.escape(w))
    if not notes:
        notes.append("全部设备均已接入链路，无孤立节点")

    cat_cn = {
        "SOURCE": "音源", "WIRELESS_MIC": "无线话筒", "WIRELESS_RX": "无线接收机",
        "ANTENNA": "天线", "ANT_DIST": "天线分配器", "MIC_HOST": "话筒主机",
        "MIXER": "调音台", "PROCESSOR": "处理器", "SPEAKER_MGR": "音箱管理器",
        "AMP": "功放", "SPEAKER": "扬声器", "SWITCH": "交换机", "IO": "接口",
    }
    agg = collections.OrderedDict()
    for i in proj.instances:
        k = (i.category, str(i.model or ""))
        agg[k] = agg.get(k, 0) + 1
    rows = "".join(
        f'<tr><td class="cat">{cat_cn.get(c, c)}</td>'
        f'<td>{html.escape(m) or "—"}</td><td class="n">{n}</td></tr>'
        for (c, m), n in sorted(agg.items(), key=lambda x: (-x[1], x[0])))

    exc = ""
    if dropped:
        names = [html.escape(str(d.get("设备名称") or "")[:22]) for d in dropped[:12]]
        exc = ('<h2 style="margin-top:18px">已排除（非信号/配件）</h2>'
               '<div class="sub">' + "、".join(names) + "</div>")

    src = os.path.basename(path)
    sub = (f'来源 {html.escape(src)}'
           + (f' · 工作表「{html.escape(sheet)}」' if sheet else '')
           + (" · 主图" if is_main else ""))

    svg = r.get("svg", "")
    status = "".join(badges) + "".join(
        f'<div class="note{" ok" if i == 0 and len(notes) == 1 else ""}">'
        f'{html.escape(t)}</div>' for i, t in enumerate(notes))
    doc = (TPL.replace("__TITLE__", html.escape(name))
              .replace("__SUB__", sub)
              .replace("__SVG__", svg)
              .replace("__STATUS__", status)
              .replace("__ROWS__", rows)
              .replace("__EXCLUDED__", exc))

    os.makedirs(OUT_DIR, exist_ok=True)
    p_html = os.path.join(OUT_DIR, name + ".html")
    with open(p_html, "w", encoding="utf-8") as f:
        f.write(doc)

    # DXF
    p_dxf = ""
    try:
        er = app._dispatch("/api/export-save", json.dumps({
            "bom": to_bom_csv(entries), "name": name,
            "dir": os.path.abspath(OUT_DIR), "require_legend": False}))
        p_dxf = er.get("path") or ""
        # 导出接口固定输出 AVCAD.dxf，需按方案重命名，否则互相覆盖
        if p_dxf and os.path.exists(p_dxf):
            new = os.path.join(OUT_DIR, name + ".dxf")
            os.replace(p_dxf, new)
            p_dxf = new
    except Exception as ex:  # noqa: BLE001
        p_dxf = f"(DXF 导出失败: {ex})"

    print(f"  ✓ {name:34s} 设备{len(proj.instances):3d} "
          f"连线{len(proj.connections):3d} | {os.path.basename(p_html)}"
          + (f" + {os.path.basename(p_dxf)}" if p_dxf and "/" in p_dxf else ""))
    return p_html


def main():
    print("渲染三份清单的系统图：")
    outs = []
    for name, path, sheet, is_main in JOBS:
        p = render(name, path, sheet, is_main)
        if p:
            outs.append(p)
    print(f"\n输出目录：{os.path.abspath(OUT_DIR)}")
    print(f"共 {len(outs)} 份")


if __name__ == "__main__":
    main()
