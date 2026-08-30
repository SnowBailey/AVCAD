"""端到端工作流管线（清单驱动 5 步串接）。

run_workflow(bom_text, decisions, redundancy, name) -> dict:
  ① 解析清单  → ② 模块确认(排除「不需要」)  → ③ 图例回填(缓存命中即套用)
  → ④ 参考架构选择(含 SPOF 建议)  → ⑤ 构建工程 + 校验  → 出图。

返回 project / excluded(排除记录) / cache_miss(待定义图例的型号) / architecture。

- cache_miss：本次清单中缓存未命中的 (brand,model)，供 UI 提示用户定义图例（确认即存）。
- 不自动把规格默认值写回缓存，避免「未确认即视为已确认」。
"""
from __future__ import annotations
import os
import tempfile
import time
from typing import Optional

from avcad.parse.bom_parser import parse_bom
from avcad.core.build import build_project, _apply_redundancy
from avcad.workflow.module_confirm import confirm_modules
from avcad.workflow.legend_store import LegendStore
from avcad.workflow.architecture import recommended


def _parse(bom_text: str) -> list:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(bom_text)
        tmp = f.name
    try:
        return parse_bom(tmp)
    finally:
        os.unlink(tmp)


def run_workflow(bom_text: Optional[str] = None, decisions: Optional[dict] = None,
                 redundancy: Optional[str] = None, name: Optional[str] = None,
                 legend_store: Optional[LegendStore] = None,
                 entries: Optional[list] = None) -> dict:
    store = legend_store or LegendStore()
    # entries 优先（UI 已解析并缓存，避免重复解析/主库补全）；否则从 bom_text 解析
    if entries is not None:
        _entries = entries
    else:
        _entries = _parse(bom_text) if bom_text else []

    # ② 模块确认 / 排除
    t0 = time.perf_counter()
    filtered, excluded = confirm_modules(_entries, decisions)

    # ③ 图例缓存命中检测（未命中=需定义，供 UI 提示）
    seen = {}
    for e in filtered:
        b, m = e.get("brand", ""), e.get("model", "")
        key = store.key(b, m)
        seen.setdefault(key, store.has(b, m))
    cache_miss = [k for k, hit in seen.items() if not hit]

    # 冗余注入（与 UI /api/export 一致）；redundancy 参数为唯一权威来源，
    # 应用前先清除条目自带冗余，避免与 BOM 已标注冗余叠加成双主备。
    if redundancy in ("PROCESSOR_BACKUP", "LINK_BACKUP", "FULL_CHAIN"):
        for e in filtered:
            e["redundancy"] = "NONE"
            e.pop("pair", None)
        filtered = _apply_redundancy(filtered, {"MIXER": redundancy})
        if redundancy == "FULL_CHAIN":
            filtered = _apply_redundancy(filtered, {"PROCESSOR": redundancy})

    # ⑤ 构建 + 校验（实例建成后按缓存图例回填端口）
    proj = build_project(filtered, name=name or "AV System", legend_store=store)

    # ④ 参考架构选择
    arch = recommended(filtered, redundancy)

    return {
        "project": proj,
        "excluded": excluded,
        "cache_miss": cache_miss,
        "architecture": arch,
        "store": store,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def summarize(result: dict) -> str:
    proj = result["project"]
    arch = result["architecture"]
    err = sum(1 for i in proj.issues if i.level == "ERROR")
    warn = sum(1 for i in proj.issues if i.level == "WARN")
    lines = [
        f"工程: {proj.name}",
        f"推荐架构: {arch[0].title}（评分 {arch[1]:.0f}）",
        f"设备 {len(proj.instances)} 台 | 连线 {len(proj.connections)} 条 | "
        f"交换机 {len(proj.switches)} 台",
        f"错误 {err} / 警告 {warn}",
        f"排除模块 {len(result['excluded'])} 个 | 待定义图例 {len(result['cache_miss'])} 个",
    ]
    for n in arch[2]:
        lines.append(f"  · 架构建议: {n}")
    for i in proj.issues:
        if i.level == "ERROR":
            lines.append(f"  [ERROR] {i.code}: {i.msg}")
    return "\n".join(lines)
