#!/usr/bin/env python3
"""样本出图：证明「可准确匹配 + 可准确生成设备图」。

- 对每一个可出图的音频类别（V1），从易科型号库里挑一个代表性产品，
  走 catalog_resolver 解析 -> expand_instance -> compute_geometry -> 渲染单设备 SVG。
- 另用一份完整 BOM（仅 brand+model+qty）跑通 build_project 全流程，
  证明「画图清单只有品牌+型号+数量」即可自动出系统图。

输出：deliverables/catalog_samples/ 下各单设备 SVG + 一张集成系统图 + index.html 画廊。
"""
from __future__ import annotations
import json, os, sys, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # avcad/
sys.path.insert(0, str(ROOT))

from avcad.model.specs import load_specs, expand_instance
from avcad.layout.blocks import compute_geometry
from avcad.render.draw import draw_devices, draw_wires, draw_ports
from avcad.render.svg_render import render_svg
from avcad.render.primitives import Canvas
from avcad.parse.product_resolver import resolve
from avcad.data.catalog_resolver import DRAW_EXCLUDE_BRANDS
from avcad.core.build import build_project

CAT = json.load(open(ROOT / "avcad" / "data" / "eko_catalog.json", encoding="utf-8"))["products"]
OUT = ROOT / "deliverables" / "catalog_samples"
OUT.mkdir(parents=True, exist_ok=True)

DRAWABLE = ["SOURCE", "WIRELESS_MIC", "ANTENNA", "ANT_DIST", "WIRELESS_RX",
            "MIXER", "PROCESSOR", "SPEAKER_MGR", "AMP", "SPEAKER", "SWITCH", "IO"]


def _proj(instances, switches=None):
    return types.SimpleNamespace(instances=instances, switches=switches or [])


def pick_example(cat: str) -> dict:
    best, any_ = None, None
    for p in CAT:
        if p["category"] != cat:
            continue
        if (p["brand"] or "").upper() in DRAW_EXCLUDE_BRANDS:
            continue
        if any_ is None:
            any_ = p
        if p.get("features"):
            best = p
            break
    return best or any_


def render_single(p: dict):
    entry = {"brand": p["brand"], "model": p["model"], "quantity": 1}
    resolve(entry)
    spec = load_specs().get(entry.get("category"))
    if not spec:
        return None
    inst = expand_instance(spec, entry, 0)
    inst.x, inst.y = 30, 24
    compute_geometry(inst)
    cv = Canvas(bg="#16161a")
    draw_devices(cv, _proj([inst]))
    draw_ports(cv, _proj([inst]))
    return render_svg(cv), entry, inst


def render_system(entries):
    proj = build_project(entries, name="易科型号库·集成样例")
    cv = Canvas(bg="#16161a")
    # 先画模块主体 → 再画连线 → 最后画端口：确保连线不盖住模块，端口在最上层
    draw_devices(cv, proj)
    draw_wires(cv, proj)
    draw_ports(cv, proj)
    return render_svg(cv), proj


def main():
    gallery = []
    for cat in DRAWABLE:
        p = pick_example(cat)
        if not p:
            print(f"[skip] {cat}: 型号库无样本")
            continue
        res = render_single(p)
        if not res:
            print(f"[skip] {cat}: 无设备模板")
            continue
        svg, entry, inst = res
        fn = OUT / f"single_{cat.lower()}.svg"
        fn.write_text(svg, encoding="utf-8")
        gallery.append({
            "cat": cat, "svg": fn.name,
            "brand": entry.get("brand"), "model": entry.get("model"),
            "name": entry.get("name"), "features": entry.get("features"),
            "params": entry.get("params"), "ports": len(inst.ports),
            "resolved": entry.get("_resolved"),
        })
        print(f"[ok] {cat:12} {entry.get('brand')} {entry.get('model')} "
              f"(ports={len(inst.ports)}, feats={entry.get('features')})")

    # 集成系统：仅用 brand+model+qty（注意：Community/Apart/Green-GO 按阳哥要求不画图）
    # 本次样本覆盖：EAW / YAMAHA / ezacoustics / IPS / Powersoft / Symetrix / AUDIX
    bom = [
        # 无线子系统
        {"brand": "AUDIX", "model": "AP41 OM2 A", "quantity": 1},     # 无线手持话筒
        {"brand": "AUDIX", "model": "ANTDA4161", "quantity": 2},      # 有源定向天线
        {"brand": "AUDIX", "model": "ADS48", "quantity": 1},          # 天线分配器
        {"brand": "IPS", "model": "CF6804", "quantity": 1},           # 四通道无线接收机
        # 有线音源
        {"brand": "AUDIX", "model": "OM2", "quantity": 2},            # 有线人声话筒
        # I/O 扩展 + 核心处理
        {"brand": "YAMAHA", "model": "RPio622", "quantity": 1},       # 扩展接口箱
        {"brand": "YAMAHA", "model": "CS-R10", "quantity": 1},          # 数字调音台台面
        {"brand": "Symetrix", "model": "Jupiter4", "quantity": 1},     # 数字音频处理器
        {"brand": "ezacoustics", "model": "ESM0408", "quantity": 1},   # 数字音箱管理器
        # 功率 + 扬声器（无源接功放 / 有源 Dante 直联）
        {"brand": "Powersoft", "model": "Ottocanali 4K4", "quantity": 1},  # 8通道功放
        {"brand": "EAW", "model": "SB210 Black", "quantity": 2},      # 无源超低
        {"brand": "EAW", "model": "ANYA V2 Black", "quantity": 2},    # 有源全频
    ]
    svg, proj = render_system(bom)
    (OUT / "system.svg").write_text(svg, encoding="utf-8")
    print(f"[ok] system.svg  实例={len(proj.instances)} 连线={len(proj.connections)} 交换机={len(proj.switches)}")

    # 画廊 HTML
    cards = []
    for g in gallery:
        cards.append(f"""
  <section class="card">
    <h3>{g['cat']} · {g['brand']} {g['model']}</h3>
    <object data="{g['svg']}" type="image/svg+xml" class="glyph"></object>
    <table>
      <tr><th>名称</th><td>{g['name']}</td></tr>
      <tr><th>特性</th><td>{', '.join(g['features'] or []) or '—'}</td></tr>
      <tr><th>参数</th><td>{json.dumps(g['params'], ensure_ascii=False)}</td></tr>
      <tr><th>端口数</th><td>{g['ports']}</td></tr>
      <tr><th>命中源</th><td>{g['resolved']}</td></tr>
    </table>
  </section>""")
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>易科型号库 · 设备出图样本</title>
<style>
 body{{background:#16161a;color:#e8e6df;font-family:PingFang SC,Microsoft YaHei,sans-serif;margin:0;padding:24px}}
 h1{{font-size:20px}} .sub{{color:#9a978d;margin-bottom:18px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}}
 .card{{background:#1f1f24;border:1px solid #2c2c33;border-radius:10px;padding:14px}}
 .card h3{{margin:2px 0 10px;font-size:14px}}
 .glyph{{width:100%;height:200px;background:#16161a;border-radius:6px}}
 table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}}
 th,td{{border:1px solid #2c2c33;padding:4px 6px;text-align:left;vertical-align:top}}
 th{{color:#9a978d;width:64px;font-weight:600}}
 .sys{{margin-top:26px}} .sys object{{width:100%;height:620px;background:#16161a;border:1px solid #2c2c33;border-radius:8px}}
</style></head><body>
<h1>易科国际型号库 · 设备出图样本（音频 V1）</h1>
<div class="sub">左：各音频类别代表性产品（名称/品牌/型号三行标题；卡槽设备以槽位可视化；特殊接口已人工覆盖）；下：完整 BOM（仅品牌+型号+数量）自动生成的集成系统图（模块在下、连线在中、端口在上）。</div>
<div class="grid">{''.join(cards)}</div>
<div class="sys"><h2>集成系统图（BOM 仅含 品牌+型号+数量；覆盖 EAW / YAMAHA / ezacoustics / IPS / Powersoft / Symetrix / AUDIX）</h2>
<object data="system.svg" type="image/svg+xml"></object></div>
</body></html>"""
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print(f"[ok] index.html  ({len(gallery)} 个单设备样本)")


if __name__ == "__main__":
    main()
