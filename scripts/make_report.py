#!/usr/bin/env python3
"""生成《易科国际型号库 · 匹配与出图报告》（音频 V1）。

输出：
  deliverables/catalog_report.md            可读报告（统计 + 清单样本）
  deliverables/catalog_report_deferred.csv  全部需人工/后置产品清单（供回填参数）
  deliverables/catalog_report.json          机器可读统计
"""
from __future__ import annotations
import csv, json, os, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "avcad" / "data" / "eko_catalog.json"
OUT = ROOT / "deliverables"
OUT.mkdir(exist_ok=True)

from avcad.data.catalog_resolver import CAT_SPEC, DRAW_EXCLUDE_BRANDS

DRAWABLE = set(CAT_SPEC.keys())          # 11 个有设备模板的类别
CAT_CN = {
    "SOURCE": "音源/话筒", "WIRELESS_MIC": "无线话筒", "ANTENNA": "天线",
    "ANT_DIST": "天线分配器", "WIRELESS_RX": "无线接收机", "MIXER": "调音台",
    "PROCESSOR": "处理器", "SPEAKER_MGR": "扬声器管理器", "AMP": "功放",
    "SPEAKER": "扬声器", "SWITCH": "Dante 交换机", "IO": "音频 I/O 箱",
}
REASON_CN = {
    "音频未识别": "分类关键词未命中（多为配件/线缆/耗材/非音频）",
    "音频配件(需人工)": "明确配件/耗材/软件/安装件，不应出图",
    "视频(V3)": "视频设备（V3 后期）",
    "电源/PDU(非信号)": "电源/PDU（非信号链）",
    "中控(V2)": "中控设备（V2 扩充）",
    "灯光(V3)": "灯光设备（V3 后期）",
    "通讯(V2)": "内部通讯设备（V2 扩充）",
}


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    P = d["products"]
    total = len(P)

    audio = [p for p in P if p["category"] in DRAWABLE]
    # 可出图 = 有模板 且 品牌未被排除（Green-GO / Community / Apart 阳哥指定不画）
    drawable = [p for p in audio
                if (p["brand"] or "").upper() not in DRAW_EXCLUDE_BRANDS]
    excluded_recognized = [p for p in audio
                           if (p["brand"] or "").upper() in DRAW_EXCLUDE_BRANDS]
    deferred = [p for p in P if p["category"] is None]
    io = [p for p in P if p["category"] == "IO"]

    cat_c = Counter(p["category"] for p in drawable)
    reason_c = Counter(p["defer_reason"] for p in deferred)

    # 品牌覆盖
    brands = defaultdict(lambda: [0, 0, 0])            # 总数, 音频命中, 需人工
    for p in P:
        b = p["brand"] or "(空)"
        brands[b][0] += 1
        if p["category"] in DRAWABLE:
            brands[b][1] += 1
        elif p["category"] is None:
            brands[b][2] += 1

    # 拆分后置原因：音频范围内待人工补参数 vs 已正确分类但超出 V1 范围
    SCOPE_DEFERRED = {"视频(V3)", "中控(V2)", "灯光(V3)", "通讯(V2)", "电源/PDU(非信号)"}
    audio_unknown = [p for p in deferred if (p["defer_reason"] or "") not in SCOPE_DEFERRED]
    out_of_scope = [p for p in deferred if (p["defer_reason"] or "") in SCOPE_DEFERRED]

    # ---- CSV：需人工清单 ----
    csv_path = OUT / "catalog_report_deferred.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["品牌", "型号", "名称", "分组", "后置原因", "类型", "处理建议"])
        for p in sorted(deferred, key=lambda x: (x["defer_reason"] or "", x["brand"], x["model"])):
            r = p["defer_reason"] or ""
            if r in SCOPE_DEFERRED:
                typ, advice = "超V1范围(已分类)", "V1 不处理；V2/V3 阶段按规划纳入"
            else:
                typ, advice = "音频待补参数", "人工确认类别或补主要参数后可出图"
            w.writerow([p["brand"], p["model"], p["name"], p.get("group"), r, typ, advice])

    # ---- JSON ----
    stats = {
        "total": total,
        "audio_recognized": len(audio),
        "drawable": len(drawable),
        "excluded_brand_recognized": len(excluded_recognized),
        "io_pending_template": 0,
        "deferred": len(deferred),
        "audio_unknown_needs_backfill": len(audio_unknown),
        "out_of_scope_deferred": len(out_of_scope),
        "by_category": dict(cat_c),
        "by_defer_reason": dict(reason_c),
        "drawable_categories": sorted(DRAWABLE),
    }
    (OUT / "catalog_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- Markdown ----
    def tline(k, v):
        return f"| {k} | {v} |"

    lines = []
    lines.append("# 易科国际型号库 · 匹配与出图报告（音频 V1）\n")
    lines.append(f"> 数据源：`260717-易科国际-产品资料清单V49.xlsx`  ")
    lines.append(f"> 生成：非流式批量解析（`scripts/build_catalog.py`）+ 解析器（`avcad/data/catalog_resolver.py`）\n")

    lines.append("## 1. 总体统计\n")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | ---: |")
    lines.append(tline("产品总数", total))
    lines.append(tline("音频可识别（命中类别）", len(audio)))
    lines.append(tline("**可出图**（有模板且品牌允许）", len(drawable)))
    lines.append(tline("已识别但指定不画（品牌剔除）", len(excluded_recognized)))
    lines.append(tline("需人工 / 后置", len(deferred)))
    lines.append("")

    lines.append("## 2. 音频类别分布（可出图）\n")
    lines.append("| 类别 | 中文 | 数量 | 可出图 |")
    lines.append("| --- | --- | ---: | --- |")
    for cat, cnt in cat_c.most_common():
        ok = "✅" if cat in DRAWABLE else "—"
        lines.append(f"| {cat} | {CAT_CN.get(cat, cat)} | {cnt} | {ok} |")
    if excluded_recognized:
        names = "、".join(sorted({p["brand"] for p in excluded_recognized}))
        lines.append(f"\n> 注：{names} 已被正确识别类别，但按阳哥要求不画出图（已排除在可出图统计外）。")
    lines.append("")

    lines.append("## 3. 后置 / 未识别分布（需人工）\n")
    lines.append("| 后置原因 | 数量 | 说明 |")
    lines.append("| --- | ---: | --- |")
    for r, c in sorted(reason_c.items(), key=lambda x: -x[1]):
        lines.append(f"| {r} | {c} | {REASON_CN.get(r, '')} |")
    lines.append("")

    lines.append("## 4. 各品牌覆盖（命中率 Top 15）\n")
    lines.append("| 品牌 | 总数 | 音频命中 | 命中率 | 需人工 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    top = sorted(brands.items(), key=lambda x: -x[1][0])[:15]
    for b, (tot, hit, df) in top:
        rate = f"{hit / tot * 100:.0f}%" if tot else "—"
        lines.append(f"| {b} | {tot} | {hit} | {rate} | {df} |")
    lines.append("")

    lines.append("## 5. 需人工处理清单\n")
    lines.append(f"后置/未识别共 **{len(deferred)}** 条，分两类：\n")
    lines.append(f"- **音频范围内待补参数：{len(audio_unknown)} 条**（`音频未识别` + `音频配件`）—— 已被收录但分类/参数缺失，需人工确认类别或回填参数后可出图。")
    lines.append(f"- **超出 V1 范围（已正确分类，按规划后置）：{len(out_of_scope)} 条**（`视频/中控/灯光/通讯/电源`）—— V1 音频不做，V2/V3 阶段纳入，无需补参数。\n")

    lines.append("### 5a. 音频待补参数清单（前 30 条，完整见 CSV）\n")
    lines.append("| 品牌 | 型号 | 名称 | 后置原因 |")
    lines.append("| --- | --- | --- | --- |")
    for p in sorted(audio_unknown, key=lambda x: (x["defer_reason"] or "", x["brand"]))[:30]:
        lines.append(f"| {p['brand']} | {p['model']} | {p['name']} | {p['defer_reason']} |")
    lines.append("")

    lines.append("## 6. 复用方式\n")
    lines.append("```bash")
    lines.append("# 1) 重新解析型号库（公司模板更新后重跑）")
    lines.append("python scripts/build_catalog.py \"/path/新清单.xlsx\"")
    lines.append("")
    lines.append("# 2) 在出图流程中按 品牌+型号+数量 自动匹配（确定性，无 LLM）")
    lines.append("from avcad.parse.product_resolver import enrich")
    lines.append("bom = [{'brand':'Powersoft','model':'Ottocanali 4K4','quantity':2}, ...]")
    lines.append("enrich(bom)   # 就地补全 category/features/params")
    lines.append("```")
    lines.append("")
    lines.append(f"完整清单：`deliverables/catalog_report_deferred.csv`（{len(deferred)} 条，含「类型/处理建议」列）")

    (OUT / "catalog_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] catalog_report.md")
    print(f"[ok] catalog_report.json  (audio={len(audio)}, drawable={len(drawable)}, deferred={len(deferred)})")
    print(f"[ok] catalog_report_deferred.csv  ({len(deferred)} 条)")


if __name__ == "__main__":
    main()
