"""模块确认 / 排除管线（清单驱动工作流 步骤②）。

把 BOM 条目按 (category, brand, model) 去重为「模块清单」（带总数量），
用户对每个模块做 include / exclude 决策。「不需要」的模块被排除出本次出图，
但保留记录（excluded）以便审计 / 后续回填，不破坏原始清单。

决策键：支持 "brand::model" 精确键，也支持仅 "model" 简写键。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ModuleItem:
    category: str
    brand: str
    model: str
    name: str
    quantity: int
    lines: List[int] = field(default_factory=list)   # 对应 entries 中的行索引
    decision: str = "include"                          # include / exclude


def build_module_list(entries: List[dict]) -> List[ModuleItem]:
    """按 (category, brand, model) 去重，汇总数量，记录原始行号。"""
    groups = {}
    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            # 防御：跳过非字典条目（如异常缓存/解析结果）
            continue
        cat = str(e.get("category", "")).upper()
        brand = (e.get("brand") or "").strip()
        model = (e.get("model") or "").strip()
        key = (cat, brand, model)
        g = groups.get(key)
        if g is None:
            g = {"qty": 0, "lines": [], "name": e.get("name") or model or cat}
            groups[key] = g
        g["qty"] += int(e.get("quantity", 1) or 1)
        g["lines"].append(idx)
    items = []
    for (cat, brand, model), g in groups.items():
        items.append(ModuleItem(cat, brand, model, g["name"], g["qty"], g["lines"]))
    return items


def _decision_for(decisions: Optional[dict], cat: str, brand: str, model: str) -> str:
    if not decisions:
        return "include"
    full = f"{brand}::{model}"
    if full in decisions:
        return decisions[full]
    if model in decisions:
        return decisions[model]
    return "include"


def confirm_modules(entries: List[dict], decisions: Optional[dict] = None
                    ) -> Tuple[List[dict], List[ModuleItem]]:
    """返回 (过滤后的 entries, 被排除的模块清单)。

    - 过滤后的 entries：仅含 include 的模块，可直接送 build。
    - excluded：被「不需要」的模块记录（保留原始信息，不出图）。
    """
    items = build_module_list(entries)
    excluded: List[ModuleItem] = []
    keep = [True] * len(entries)
    for it in items:
        d = _decision_for(decisions, it.category, it.brand, it.model)
        it.decision = d
        if d == "exclude":
            excluded.append(it)
            for li in it.lines:
                keep[li] = False
    filtered = [e for i, e in enumerate(entries) if keep[i]]
    return filtered, excluded
