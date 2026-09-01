"""模块确认 / 排除管线（清单驱动工作流 步骤②）。

把 BOM 条目按 (category, brand, model) 去重为「模块清单」（带总数量），
用户对每个模块做 include / exclude 决策。「不需要」的模块被排除出本次出图，
但保留记录（excluded）以便审计 / 后续回填，不破坏原始清单。

决策键：支持 "brand::model" 精确键，也支持仅 "model" 简写键。

``ModuleItem.source`` 记录该型号在主库里的收录状态（catalog / builtin /
deferred / unknown）。★ 2026-09-01 新增：此前 ``_resolved`` 从不进 UI，
配单里出现「主库没有的新型号」时用户**完全看不到**——它会被静默兜底成 IO
类别，出图后是个孤立方块，校验层还不报警。现在第②步会给它打徽章。
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
    source: str = "unknown"                            # catalog/builtin/deferred/unknown
    # 代表条目的特性与参数：图例页要用它们让引擎展开端口初值
    # （io.yaml 的 DANTE 口要 dante 特性，mixer 的进数要看 inputs 参数）
    features: List[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def _source_mark(e: dict) -> str:
    """把 ``_resolved`` 归一化成四态收录标记。

    catalog  = 主库命中且可出图
    deferred = 主库命中但被后置（配件/线缆/非音频）
    builtin  = 主库没有，回退到内置 MODEL_DB
    unknown  = 两库都没有 —— 类别是靠名称关键词猜的，或兜底成 IO
    """
    r = str(e.get("_resolved") or "")
    if r.startswith("eko-deferred") or r.startswith("eko-no-draw"):
        return "deferred"
    if r.startswith("eko"):
        return "catalog"
    if r.startswith("kb"):
        return "builtin"
    return "unknown"


def _ensure_resolved(entries: List[dict]) -> List[dict]:
    """给缺 ``_resolved`` 的条目补打主库命中标记（**不修改调用方的对象**）。

    xlsx 走 ``importers.build_entries`` 时已经 resolve 过；但从 BOM CSV 重建的
    条目（``parse_bom``）没有这个标记——CSV 里没有这一列，``to_bom_csv`` 也不导出，
    所以 ``/api/modules`` 拿到的条目是「裸」的。这里用**浅拷贝**补一次 resolve，
    避免就地污染 ``app._ENTRY_CACHE`` 里缓存的条目对象。
    """
    from ..parse.product_resolver import enrich
    out = []
    for e in entries:
        if not isinstance(e, dict) or e.get("_resolved"):
            out.append(e)
            continue
        c = dict(e)
        enrich([c])
        out.append(c)
    return out


def build_module_list(entries: List[dict]) -> List[ModuleItem]:
    """按 (category, brand, model) 去重，汇总数量，记录原始行号与收录状态。"""
    groups = {}
    for idx, e in enumerate(_ensure_resolved(entries)):
        if not isinstance(e, dict):
            # 防御：跳过非字典条目（如异常缓存/解析结果）
            continue
        cat = str(e.get("category", "")).upper()
        brand = (e.get("brand") or "").strip()
        model = (e.get("model") or "").strip()
        key = (cat, brand, model)
        g = groups.get(key)
        if g is None:
            g = {"qty": 0, "lines": [], "name": e.get("name") or model or cat,
                 "source": _source_mark(e),
                 "features": [str(x) for x in (e.get("features") or [])],
                 "params": dict(e.get("params") or {})}
            groups[key] = g
        g["qty"] += int(e.get("quantity", 1) or 1)
        g["lines"].append(idx)
    items = []
    for (cat, brand, model), g in groups.items():
        items.append(ModuleItem(cat, brand, model, g["name"], g["qty"], g["lines"],
                                "include", g["source"], g["features"], g["params"]))
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
