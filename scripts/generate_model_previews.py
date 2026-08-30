#!/usr/bin/env python3
"""为品牌确认流程预生成所有型号的单设备 SVG 预览与清单 manifest.json。"""
from __future__ import annotations
import json, os, sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avcad.model.specs import load_specs, expand_instance
from avcad.layout.blocks import compute_geometry
from avcad.render.draw import draw_devices, draw_ports
from avcad.render.svg_render import render_svg
from avcad.parse.product_resolver import resolve
from avcad.data.catalog_resolver import DRAW_EXCLUDE_BRANDS
import types

CAT = json.load(open(ROOT / "avcad" / "data" / "eko_catalog.json", encoding="utf-8"))["products"]
OUT = ROOT / "deliverables" / "model_previews"
OUT.mkdir(parents=True, exist_ok=True)

TARGET_BRANDS = ["IPS", "ezacoustics", "EAW", "Powersoft", "Symetrix", "AUDIX", "YAMAHA"]
DRAWABLE = ["SOURCE", "WIRELESS_MIC", "ANTENNA", "ANT_DIST", "WIRELESS_RX",
            "MIXER", "PROCESSOR", "SPEAKER_MGR", "AMP", "SPEAKER", "SWITCH", "IO"]


def _proj(instances):
    return types.SimpleNamespace(instances=instances, switches=[])


def render_one(p: dict):
    entry = {"brand": p["brand"], "model": p["model"], "quantity": 1}
    resolve(entry)
    cat = entry.get("category")
    spec = load_specs().get(cat)
    if not spec:
        return None, f"无模板({cat})"
    try:
        inst = expand_instance(spec, entry, 0)
        inst.x, inst.y = 30, 24
        compute_geometry(inst)
        cv = Canvas = __import__("avcad.render.primitives", fromlist=["Canvas"]).Canvas
        canvas = Canvas(bg="#16161a")
        draw_devices(canvas, _proj([inst]))
        draw_ports(canvas, _proj([inst]))
        svg = render_svg(canvas)
        return svg, None
    except Exception as e:
        return None, f"渲染失败: {e}"


def main():
    manifest = {b: [] for b in TARGET_BRANDS}
    for brand in TARGET_BRANDS:
        brand_dir = OUT / brand
        brand_dir.mkdir(exist_ok=True)
        seen = set()
        items = []
        for p in CAT:
            if p.get("brand", "").upper() != brand.upper():
                continue
            model = p.get("model", "")
            if not model or model in seen:
                continue
            seen.add(model)
            svg, err = render_one(p)
            status = "drawable" if p.get("category") in DRAWABLE else ("deferred" if p.get("category") else "skipped")
            fn = None
            if svg:
                fn = brand_dir / f"{model.replace('/', '-').replace(' ', '_')}.svg"
                fn.write_text(svg, encoding="utf-8")
            item = {
                "brand": p["brand"],
                "model": model,
                "name": p.get("name", ""),
                "category": p.get("category"),
                "defer_reason": p.get("defer_reason"),
                "status": status,
                "svg": str(fn.relative_to(OUT)) if fn else None,
                "error": err,
            }
            items.append(item)
        # 按型号排序，drawable 在前方便先确认
        items.sort(key=lambda x: (0 if x["status"] == "drawable" else 1, x["model"]))
        manifest[brand] = items
        print(f"[ok] {brand}: {len(items)} 个型号，已生成 SVG: {sum(1 for i in items if i['svg'])}")

    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(v) for v in manifest.values())
    print(f"[ok] manifest.json 总计 {total} 个型号")


if __name__ == "__main__":
    main()
