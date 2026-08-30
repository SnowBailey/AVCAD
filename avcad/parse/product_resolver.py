"""产品解析器：按 (品牌,型号) 命中型号库。

优先使用易科国际产品资料清单（eko_catalog.json，2298 条）做主匹配；
未命中时回退到内置 MODEL_DB（节选自 ima AV 知识库的少量精修设备）。
命中 -> 抽取类别/特性/参数覆盖；未命中 -> 仅标注，回退到类别默认模板（由规格库负责）。
全程确定性，无外部 LLM 依赖。
"""
from __future__ import annotations

from ..data.catalog_resolver import resolve as _resolve_eko, load as _load_eko

# 内置型号库（节选自阳哥 ima AV 知识库设备文档；可经 sync_kb 增量扩充）
MODEL_DB = {
    ("yamaha", "tf5"): dict(category="MIXER", features=["dante", "control"],
                            params=dict(inputs=32, outputs=16), kb="数字调音台-雅马哈TF5"),
    ("biamp", "tesiraforte ai"): dict(category="PROCESSOR", proc_func="automix",
                                      features=["dante", "control"], params=dict(inputs=12, outputs=12),
                                      kb="音频处理器-Biamp TesiraFORTE AI"),
    ("biamp", "tesiraforte"): dict(category="PROCESSOR", proc_func="automix",
                                   features=["dante", "control"], params=dict(inputs=12, outputs=12)),
    ("bss", "blu-806"): dict(category="PROCESSOR", proc_func="system",
                             features=["dante", "control"], params=dict(inputs=8, outputs=8),
                             kb="音频处理器-BSS BLU-806"),
    ("bss", "blu806"): dict(category="PROCESSOR", proc_func="system",
                            features=["dante", "control"], params=dict(inputs=8, outputs=8)),
    ("powersoft", "quattrocanali 4804"): dict(category="AMP", features=["dante", "control", "analog"],
                                               params=dict(channels=4), electrical=dict(min_load_ohm=4, power_w_per_ch=1200),
                                               kb="功放-Powersoft Quattrocanali 4804"),
    ("powersoft", "quattrocanali4804"): dict(category="AMP", features=["dante", "control", "analog"],
                                              params=dict(channels=4)),
    ("l-acoustics", "kara"): dict(category="SPEAKER", active=False,
                                  params=dict(impedance_ohm=8, power_w=400), kb="扬声器-L-Acoustics KARA"),
    ("shure", "ulxd4d"): dict(category="WIRELESS_RX", features=["dante", "control"],
                              params=dict(channels=2), kb="无线接收机-Shure ULXD4D(双通道,模拟+Dante同出)"),
    ("shure", "ulxd4"): dict(category="WIRELESS_RX", features=["dante", "control"],
                             params=dict(channels=1)),
    ("sennheiser", "ew g4"): dict(category="WIRELESS_RX", features=["control"],
                                  params=dict(channels=1)),
}


def _key(brand, model):
    def n(x):
        return "".join(ch for ch in str(x).lower() if ch.isalnum())
    return (n(brand), n(model))


# 归一化 MODEL_DB 键（原键含空格，与 _key 不一致会导致回退永远不命中）
_MODEL_DB = {_key(b, m): v for (b, m), v in MODEL_DB.items()}


def resolve(entry: dict) -> dict:
    """就地用型号库覆盖 entry 的 category/features/params/proc_func/active；返回命中来源。

    匹配顺序：
      1) 易科产品资料清单（eko_catalog.json）——主库，覆盖面最广
      2) 内置 MODEL_DB——精修补充（含 proc_func / electrical 等清单未萃取的字段）
    """
    brand = entry.get("brand", "")
    model = entry.get("model", "")
    code = entry.get("code")

    # ---- 1) 主库：易科产品资料清单 ----
    ek = _resolve_eko(brand, model, code)
    if ek:
        cat = ek.get("category")
        if cat and ek.get("drawable"):
            # 可出图的核心音频设备：以清单为准
            if not entry.get("category"):
                entry["category"] = cat
            entry["_resolved"] = "eko:" + str(ek.get("code") or f"{brand} {model}")
            entry["_eko_code"] = ek.get("code")
            entry["_drawable"] = True
            entry["_template"] = ek.get("template")
        elif cat:
            # 命中但无专属模板——保留类别，标记待补模板
            if not entry.get("category"):
                entry["category"] = cat
            entry["_resolved"] = "eko:" + str(ek.get("code") or f"{brand} {model}")
            entry["_drawable"] = False
            entry["_template"] = ek.get("template")
        else:
            # 命中但被后置（音频未识别/视频/中控/电源/配件等）——不强行给类别
            entry["_resolved"] = "eko-deferred:" + str(ek.get("defer_reason") or "未识别")
            entry["_drawable"] = False
            entry["_defer_reason"] = ek.get("defer_reason")
        # 透传清单中的设备名称（阳哥要求标题按清单来）
        if ek.get("name") and not entry.get("name"):
            entry["name"] = ek["name"]
        # 合并清单萃取的特性与参数（不覆盖 entry 已有、来自其它途径的更可靠值）
        feats = set(str(x).lower() for x in entry.get("features", []) or [])
        feats |= set(str(x).lower() for x in ek.get("features", []))
        if feats:
            entry["features"] = sorted(feats)
        if ek.get("params"):
            params = dict(entry.get("params", {}))
            for k, v in ek["params"].items():
                params.setdefault(k, v)
            entry["params"] = params
        if ek.get("active") and not entry.get("active"):
            entry["active"] = ek["active"]
        # 已命中主库：仍用 MODEL_DB 补充 proc_func / electrical 等精修字段
        _apply_model_db(entry)
        return entry

    # ---- 2) 回退：内置 MODEL_DB ----
    _apply_model_db(entry)
    return entry


def _apply_model_db(entry: dict) -> None:
    brand = entry.get("brand", "")
    model = entry.get("model", "")
    hit = _MODEL_DB.get(_key(brand, model))
    if not hit:
        entry.setdefault("_resolved", "fallback")
        return
    if not entry.get("category"):
        entry["category"] = hit["category"]
    if hit.get("proc_func") and not entry.get("proc_func"):
        entry["proc_func"] = hit["proc_func"]
    if "active" in hit and not entry.get("active"):
        entry["active"] = hit["active"]
    feats = set(str(x).lower() for x in entry.get("features", []) or [])
    feats |= set(hit.get("features", []))
    entry["features"] = sorted(feats)
    params = dict(entry.get("params", {}))
    params.update(hit.get("params", {}))
    entry["params"] = params
    if hit.get("electrical"):
        entry.setdefault("electrical", {})
        entry["electrical"].update(hit["electrical"])
    entry.setdefault("_resolved", "kb:" + hit.get("kb", f"{brand} {model}"))


def enrich(entries: list) -> list:
    for e in entries:
        resolve(e)
    return entries


# 注：ima KB 全量同步可经 mcp__ima-mcp 拉取设备文档构建 MODEL_DB；此处内置节选保证离线可跑。
