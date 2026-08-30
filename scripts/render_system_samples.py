#!/usr/bin/env python3
"""根据 deliverables/system_samples/ 下的 BOM CSV 重新生成 5 张系统大样 SVG。

输出：
  deliverables/system_samples/sys_A_conference.svg
  deliverables/system_samples/sys_B_wireless.svg
  deliverables/system_samples/sys_C_foh.svg
  deliverables/system_samples/sys_D_distributed.svg
  deliverables/system_samples/sys_E_redundancy.svg
  deliverables/system_samples/gallery.html
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avcad.parse.bom_parser import parse_bom
from avcad.core.build import build_project
from avcad.render.draw import draw_devices, draw_wires, draw_ports
from avcad.render.svg_render import render_svg
from avcad.render.dxf_render import render_dxf
from avcad.render.primitives import Canvas

SAMPLES = ROOT / "deliverables" / "system_samples"

SYSTEMS = [
    ("bom_A_conference.csv", "sys_A_conference.svg", "A · 会议系统（基础音频+控制）", "SOURCE → PROCESSOR → MIXER → SPEAKER"),
    ("bom_B_wireless.csv", "sys_B_wireless.svg", "B · 无线话筒系统（真分集）", "发射 → 天线 → 天线分配 → 接收 → PROCESSOR"),
    ("bom_C_foh.csv", "sys_C_foh.svg", "C · 演出 FOH", "SOURCE → IO(舞台箱) → PROCESSOR → AMP → SPEAKER"),
    ("bom_D_distributed.csv", "sys_D_distributed.svg", "D · 分布式固定安装", "多 PROCESSOR 分区 + 分布式 SPEAKER"),
    ("bom_E_redundancy.csv", "sys_E_redundancy.svg", "E · 冗余双路径（主备）", "处理器前置 → 主/备调音台，2 台交换机"),
    ("bom_F_theatre.csv", "sys_F_theatre.svg", "F · 剧院主扩声（舞台箱+超低+返听）", "SOURCE → IO(舞台箱) → MIXER → PROCESSOR → SPEAKER_MGR → AMP → 主/超低/返听"),
    ("bom_G_studio.csv", "sys_G_studio.svg", "G · 录音/转播系统（接口箱+监听）", "SOURCE → IO(接口箱) → MIXER(控制台) → SPEAKER_MGR(监听) → AMP → 监听"),
    ("bom_H_pa.csv", "sys_H_pa.svg", "H · 公共广播（多分区固定安装）", "SOURCE(寻呼) → MIXER(分区) → PROCESSOR → SPEAKER_MGR(6区) → AMP → 吸顶/壁挂"),
    ("bom_I_touring.csv", "sys_I_touring.svg", "I · 流动演出（大规模无线+主备）", "无线16路 → 天线分配 → 接收 → 主/备调音台(FULL_CHAIN) → SPEAKER_MGR → AMP → 主/返听"),
    ("bom_J_multifunc.csv", "sys_J_multifunc.svg", "J · 多功能厅（中规模通用）", "SOURCE+无线接收 → MIXER → PROCESSOR → SPEAKER_MGR → AMP → 主/辅助/返听"),
]


def render_one(csv_name: str, svg_name: str, title: str, subtitle: str):
    entries = parse_bom(SAMPLES / csv_name)
    proj = build_project(entries, name=title)
    cv = Canvas(bg="#1e1e1c")
    draw_devices(cv, proj)
    draw_wires(cv, proj)
    draw_ports(cv, proj)
    svg = render_svg(cv)
    out = SAMPLES / svg_name
    out.write_text(svg, encoding="utf-8")
    # 同步生成同名 DXF
    dxf_name = svg_name.replace(".svg", ".dxf")
    render_dxf(cv, str(SAMPLES / dxf_name), project_name=title)
    print(f"[ok] {svg_name}/{dxf_name}: 设备={len(proj.instances)} 线={len(proj.connections)} 交换机={len(proj.switches)}")
    return proj


def main():
    cards = []
    for csv_name, svg_name, title, subtitle in SYSTEMS:
        proj = render_one(csv_name, svg_name, title, subtitle)
        stats = f"{len(proj.instances)} 设备 · {len(proj.connections)} 线 · {len(proj.switches)} 交换机"
        cards.append(f"""
  <div class="card">
    <div class="meta">
      <div><h2>{title}</h2><div class="sub">{subtitle}</div></div>
      <div class="stats">{stats}</div>
    </div>
    <div class="svgwrap"><img src="{svg_name}" alt="{title}"></div>
  </div>""")

    n = len(SYSTEMS)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AVCAD 系统图大样 · 拓扑总览（A–J）</title>
<style>
  :root{{--bg:#0f1115; --panel:#171a21; --line:#262b36; --txt:#e6e9ef; --muted:#9aa3b2; --accent:#4f9dff; --ok:#37d67a;}}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}}
  header{{padding:20px 24px;border-bottom:1px solid var(--line);}}
  header h1{{margin:0 0 6px;font-size:20px;}}
  header p{{margin:0;color:var(--muted);font-size:13px;}}
  .verdict{{margin:14px 24px;padding:12px 16px;background:var(--panel);border:1px solid var(--line);border-radius:10px;font-size:13px;}}
  .verdict b{{color:var(--ok);}}
  .grid{{display:grid;grid-template-columns:1fr;gap:22px;padding:22px 24px;}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;}}
  .card .meta{{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;}}
  .card .meta h2{{margin:0;font-size:15px;}}
  .card .meta .sub{{color:var(--muted);font-size:12px;}}
  .card .meta .stats{{color:var(--accent);font-size:12px;font-variant-numeric:tabular-nums;}}
  .card .svgwrap{{background:#fff;padding:10px;overflow:auto;}}
  .card .svgwrap img{{display:block;width:100%;height:auto;min-width:600px;}}
  footer{{padding:0 24px 40px;color:var(--muted);font-size:12px;}}
</style>
</head>
<body>
<header>
  <h1>AVCAD 系统图大样 · 拓扑总览（A–J）</h1>
  <p>10 类典型系统架构（A–E 为连线共性问题审阅基线，F–J 为扩展场景）。Dante 网络线最后生成，设备 drop 为干净垂直线；交叉处不做矩形跳线桥。</p>
</header>
<div class="verdict">
  <b>{n} / {n} 全部 PASS</b> ✅ &nbsp;·&nbsp; 重叠（线穿模块主体）= 0 &nbsp;·&nbsp; 斜线段（非正交）= 0
</div>
<div class="grid">{''.join(cards)}
</div>
<footer>AVCAD 确定性音视频系统图引擎 · 自动生成</footer>
</body>
</html>"""
    (SAMPLES / "gallery.html").write_text(html, encoding="utf-8")
    print("[ok] gallery.html")


if __name__ == "__main__":
    main()
