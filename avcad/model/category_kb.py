"""设备类别语义知识库加载器 (Device Category Knowledge Base loader).

与 device_specs/*.yaml（端口模板）配合：端口模板只定义「画哪些口」，
本知识库定义「设备是什么、上下游是谁、怎么接、匹配规则、识别线索」。

典型用法：
    from avcad.model.category_kb import get_kb, list_categories, identify
    kb = get_kb("PROCESSOR")          # 取某类完整语义
    print(identify("IPS", "UM2000ASD"))  # 给新型号 → 识别类别 + 建议接法

identify() 三级识别，让系统拿到一个「从未见过的型号」也能判断：
  1) 主库 eko_catalog 精确命中（品牌+型号，权威）→ 100% 准
  2) 型号片段 KNOWN_MODELS（项目真实型号 + 常见品牌系列）
  3) aliases 类别词/描述模糊匹配（兜底）
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import yaml  # device_specs 同样依赖，运行时已可用

_KB_PATH = Path(__file__).resolve().parent.parent / "data" / "device_kb.yaml"
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "eko_catalog.json"

# 型号片段 → 类别（识别增强，覆盖项目主库真实型号 + 常见品牌系列）。
# ★ 长/具体片段在前，避免被短串抢（如 um2000asd 先于 um2000；atw-a 先于 atw）。
# ★ 仅放「品牌+系列」级别的高置信片段；纯类别词留给 aliases 模糊匹配。
KNOWN_MODELS = [
    # —— 天线合路器（多入一出，发射侧）——
    ("um2000asd", "ANT_COMBINE"),
    ("combiner", "ANT_COMBINE"),
    # —— 天线分配器（一入多出，接收侧）——
    ("um2000atd", "ANT_DIST"),
    ("ua844", "ANT_DIST"), ("ua845", "ANT_DIST"), ("ua84", "ANT_DIST"),
    ("ads48", "ANT_DIST"), ("ads", "ANT_DIST"),
    # —— 天线（空中换能）——
    ("cf6300wb", "ANTENNA"),
    ("ha-", "ANTENNA"), ("a2003", "ANTENNA"), ("atw-a", "ANTENNA"),
    # —— 会议主机（手拉手/同传）——
    ("cf6300", "MIC_HOST"),
    ("hcs-", "MIC_HOST"), ("ccs-", "MIC_HOST"), ("taiden", "MIC_HOST"),
    ("gonsin", "MIC_HOST"),
    # —— 无线话筒/接收（UM2000 系列本身是接收落地，归接收侧）——
    ("um2000", "WIRELESS_RX"),
    ("ulxd", "WIRELESS_RX"), ("ulx", "WIRELESS_RX"),
    ("qlxd", "WIRELESS_RX"), ("qlx", "WIRELESS_RX"),
    ("slxd", "WIRELESS_RX"), ("slx", "WIRELESS_RX"),
    ("ew100", "WIRELESS_RX"), ("ew300", "WIRELESS_RX"), ("ew500", "WIRELESS_RX"),
    ("atw", "WIRELESS_RX"),
    # —— 音源（播放/录音/声卡）——
    ("cdj", "SOURCE"), ("dn-", "SOURCE"), ("cd-", "SOURCE"),
    ("motu", "SOURCE"), ("tascam", "SOURCE"), ("focusrite", "SOURCE"),
    # —— 音频接口箱（远端 I/O 边界）——
    ("rdd", "IO"), ("rio", "IO"), ("dx168", "IO"), ("dl16", "IO"), ("dl32", "IO"),
    # —— 调音台（人工混音母线）——
    ("tf5", "MIXER"), ("tf3", "MIXER"), ("tf1", "MIXER"), ("tf", "MIXER"),
    ("ql5", "MIXER"), ("ql1", "MIXER"), ("ql", "MIXER"),
    ("cl5", "MIXER"), ("cl3", "MIXER"), ("cl1", "MIXER"), ("cl", "MIXER"),
    ("dm7", "MIXER"), ("dm3", "MIXER"), ("dm", "MIXER"),
    ("sq", "MIXER"), ("dlive", "MIXER"), ("x32", "MIXER"), ("m32", "MIXER"),
    ("qu", "MIXER"), ("mgp", "MIXER"), ("si", "MIXER"),
    # —— 处理器（路由/矩阵/算法）——
    ("tesira", "PROCESSOR"), ("core", "PROCESSOR"),
    ("neutrino", "PROCESSOR"), ("blu", "PROCESSOR"), ("matrix", "PROCESSOR"),
    # —— 音箱管理器（专管音箱分频限幅）——
    ("driverack", "SPEAKER_MGR"), ("lm26", "SPEAKER_MGR"),
    ("armon", "SPEAKER_MGR"), ("lake", "SPEAKER_MGR"),
    # —— 功放 ——
    ("xls", "AMP"), ("x4", "AMP"), ("crown", "AMP"), ("powersoft", "AMP"),
    # —— 音箱（发声终点）——
    ("k2", "SPEAKER"), ("k1", "SPEAKER"), ("vtx", "SPEAKER"),
    ("kara", "SPEAKER"), ("kf", "SPEAKER"),
    ("d&b", "SPEAKER"), ("jbl", "SPEAKER"), ("eaw", "SPEAKER"),
]


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with open(_KB_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=1)
def _catalog_index() -> dict:
    """主库 (brand.upper(), model.upper()) → category 索引。"""
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {
            (str(x.get("brand", "")).upper(), str(x.get("model", "")).upper()): x.get("category")
            for x in d.get("products", [])
        }
    except Exception:
        return {}


def get_kb(category: str) -> dict:
    """取某类别的完整语义知识；未知类别返回空 dict。"""
    return _load().get(category, {})


def list_categories() -> list[str]:
    """所有已知类别码。"""
    return list(_load().keys())


def category_cn(category: str) -> str:
    """类别码 → 中文名（未知返回原码）。"""
    return _load().get(category, {}).get("cn", category)


def _build(cat: str, method: str, conf: float) -> dict:
    kb = _load()
    info = kb.get(cat, {})
    up = "/".join(info.get("upstream", [])) or "无"
    down = "/".join(info.get("downstream", [])) or "无"
    return {
        "category": cat,
        "cn": info.get("cn", cat),
        "role": info.get("role", ""),
        "confidence": conf,
        "suggest": f"[{method}] 上游[{up}] → {info.get('cn', cat)} → 下游[{down}]",
    }


def identify(brand: str = "", model: str = "", extra: str = "") -> dict:
    """根据品牌+型号+附加描述文本识别设备类别与建议接法。

    三级回退：主库精确 → 型号片段 → 类别词。
    返回 {category, cn, role, confidence, suggest}；未识别 category=None。
    """
    text = f"{brand} {model} {extra}".lower()

    # 1) 主库精确（品牌+型号）
    cat_idx = _catalog_index()
    key = (brand.upper(), model.upper())
    if key in cat_idx and cat_idx[key]:
        return _build(cat_idx[key], "主库命中", 1.0)

    # 2) 型号片段
    for frag, cat in KNOWN_MODELS:
        if frag in text:
            return _build(cat, "型号片段匹配", 0.8)

    # 3) aliases 类别词/描述
    best, best_score = None, 0
    for cat, info in _load().items():
        score = sum(1 for a in info.get("aliases", []) if a.lower() in text)
        if score > best_score:
            best, best_score = cat, score
    if best is None:
        return {
            "category": None,
            "cn": "",
            "role": "",
            "confidence": 0.0,
            "suggest": "未在知识库匹配到类别，请人工指定 category。",
        }
    return _build(best, "类别词匹配", min(best_score / 3.0, 1.0))


def usage_hint(brand: str = "", model: str = "", extra: str = "") -> dict:
    """给一个型号，返回人能直接读的「这是什么 + 怎么接」。

    返回 {category, cn, role, upstream, downstream, confidence, suggest, identified}。
    identified=False 表示知识库没把握（category=None），需人工指定类别。
    供第②步模块确认页在「主库未收录」徽章旁展示识别建议。
    """
    r = identify(brand, model, extra)
    cat = r["category"]
    if not cat:
        return {
            "category": None, "cn": "", "role": "",
            "upstream": [], "downstream": [],
            "confidence": 0.0, "suggest": r["suggest"], "identified": False,
        }
    kb = get_kb(cat)
    return {
        "category": cat,
        "cn": r["cn"],
        "role": r["role"],
        "upstream": kb.get("upstream", []) or [],
        "downstream": kb.get("downstream", []) or [],
        "confidence": r["confidence"],
        "suggest": r["suggest"],
        "identified": True,
    }


if __name__ == "__main__":
    # 自测：几个新型号样例
    for b, m in [
        ("IPS", "UM2000ASD"),
        ("Shure", "ULXD4"),
        ("Yamaha", "TF5"),
        ("dbx", "DriveRack PA2"),
        ("L-Acoustics", "K2"),
        ("Pioneer DJ", "CDJ-3000"),
        ("IPS", "CF6300"),
        ("IPS", "CF6300WB"),
        ("IPS", "UM2000ATD"),
        ("EZACOUSTICS", "RDD12"),
        ("ALLEN & HEATH", "QU16"),
        ("YAMAHA", "RIO3224-D"),
    ]:
        print(b, m, "->", identify(b, m))
