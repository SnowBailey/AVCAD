"""清单导入富集层：xlsx / 自由格式清单 -> 规范化引擎条目。

职责（清单驱动 5 步工作流的第①步「AI 解析品牌/型号/数量/类别」）：
- 吊架/飞行架/桁架等非信号设备：关键词命中即排除，优先级高于主库（避免主库误判为扬声器）。
- 品牌+型号优先走 product_resolver 主库（eko_catalog）补全类别/特性/参数/电气。
- 主库未覆盖或延迟（deferred，如 QU-16）的型号：用设备名关键词兜底分类 + 指标参数正则抽取。
- 功放归一化：主库给 channels_hint，需映射为 channels，并从指标参数补 power_w_per_ch 电气功率。

纯标准库 + openpyxl（已是 avcad 依赖）。不引入新依赖。
"""
from __future__ import annotations
import csv
import io
import json
import re

from avcad.parse.bom_parser import dump_params
from avcad.parse.product_resolver import enrich as resolve_products

# 非信号设备：机械/结构件，不出系统图
RIGGING = ["吊架", "飞行架", "桁架"]
# 设备名关键词 -> 类别（兜底分类，顺序即优先级）
CATEGORY_KW = [
    ("SOURCE", ["话筒", "麦克风", "传声器", "音源"]),
    ("WIRELESS_MIC", ["发射", "无线话筒"]),
    ("WIRELESS_RX", ["接收机", "接收端"]),
    ("ANTENNA", ["天线"]),
    ("IO", ["接口箱", "接口卡", "接口矩阵"]),
    ("SWITCH", ["交换机"]),
    ("MIXER", ["调音台", "控制台", "混音"]),
    ("PROCESSOR", ["处理器", "dsp", "音频处理"]),
    ("AMP", ["功率放大器", "功放", "放大器"]),
    ("SPEAKER", ["扬声器", "音箱", "全频", "线阵列", "低音", "补声", "返送",
                 "返听", "主扩", "拉声像", "台唇", "环绕", "音柱", "扩声", "号筒"]),
]
# 控制/网络特性关键词
CONTROL_KW = ["集控", "网络监测", "网络检测", "tcp/ip", "rs-232", "rs-485",
              "gpio", "控制", "状态上报", "ip控制", "中控"]

_AMP_CH = re.compile(r"(\d+)\s*通道")
_AMP_POWER = re.compile(r"8Ω.*?(\d+)\s*W", re.I | re.S)
_OHM = re.compile(r"(\d+)\s*Ω")
_SP_POWER = re.compile(r"(\d+)\s*W")
_IN = re.compile(r"模拟输入\s*(\d+)\s*路")
_OUT = re.compile(r"模拟输出\s*(\d+)\s*路")


def is_rigging(name: str) -> bool:
    return any(k in (name or "") for k in RIGGING)


def classify_category(name: str):
    """返回类别或 None（吊架）。"""
    if is_rigging(name):
        return None
    for cat, kws in CATEGORY_KW:
        if any(k in (name or "") for k in kws):
            return cat
    return None


def extract_features(spec: str, name: str, category: str) -> set:
    feats = set()
    s = (spec or "").lower()
    n = (name or "").lower()
    if "dante" in s:
        feats.add("dante")
    if any(k in s or k in n for k in CONTROL_KW):
        feats.add("control")
    if category in ("PROCESSOR",) and ("模拟" in s or "analog" in s):
        feats.add("analog")
    if "有源" in (name or ""):
        feats.add("active")
    return feats


def extract_params(spec: str, category: str) -> dict:
    p = {}
    if not spec:
        return p
    if category == "AMP":
        m = _AMP_CH.search(spec)
        if m:
            p["channels"] = int(m.group(1))
        m = _AMP_POWER.search(spec)
        if m:
            p["power_w_per_ch"] = int(m.group(1))
    elif category == "SPEAKER":
        m = _OHM.search(spec)
        if m:
            p["impedance_ohm"] = int(m.group(1))
        m = _SP_POWER.search(spec)
        if m:
            p["power_w"] = int(m.group(1))
    elif category in ("PROCESSOR", "MIXER"):
        m = _IN.search(spec)
        if m:
            p["inputs"] = int(m.group(1))
        m = _OUT.search(spec)
        if m:
            p["outputs"] = int(m.group(1))
    return p


def _qty(v) -> int:
    try:
        return int(float(v or 1))
    except (ValueError, TypeError):
        return 1


def read_xlsx_rows(path: str) -> list:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h) if h is not None else "" for h in rows[0]]
    out = []
    for r in rows[1:]:
        if all(c is None for c in r):
            continue
        out.append({header[i]: r[i] for i in range(len(header))})
    return out


def build_entries(path: str):
    """读 xlsx -> 规范化条目（已排除吊架）。返回 (entries, dropped_rows)。

    entries 元素含：brand/model/name/quantity/category/features(list)/params(dict)/
    electrical(dict,仅功放)/spec(原始指标参数，仅内部用)。
    """
    raw = read_xlsx_rows(path)
    av, dropped = [], []
    for r in raw:
        name = r.get("设备名称") or r.get("名称") or r.get("name") or ""
        if is_rigging(name):
            dropped.append(r)
            continue
        # 造价/计价清单里大量「项目特征描述」续行、小计行、空行：设备名称为空
        # 一律跳过，否则会被当成无名设备导入（实测一份清单多出 150+ 条空条目）。
        if not str(name).strip():
            continue
        av.append({
            "brand": r.get("品牌") or r.get("brand") or "",
            "model": r.get("型号") or r.get("model") or "",
            "name": name,
            "quantity": _qty(r.get("数量") or r.get("qty")),
            "spec": r.get("指标参数") or r.get("参数") or r.get("spec") or "",
        })
    # 主库补全（品牌+型号）
    resolve_products(av)
    for e in av:
        cat = e.get("category")
        if not cat:
            cat = classify_category(e.get("name", "")) or "IO"
            e["category"] = cat
        spec = e.get("spec", "")
        feats = set(str(x).lower() for x in e.get("features", []) or [])
        feats |= extract_features(spec, e.get("name", ""), cat)
        e["features"] = sorted(feats)
        extracted = extract_params(spec, cat)
        params = dict(extracted)
        params.update(e.get("params", {}) or {})  # 主库值优先
        e["params"] = params
        if cat == "AMP":
            elec = dict(e.get("electrical", {}) or {})
            pw = extracted.get("power_w_per_ch")
            if pw and "power_w_per_ch" not in elec:
                elec["power_w_per_ch"] = pw
            elec.setdefault("min_load_ohm", 4)
            e["electrical"] = elec
        if "有源" in (e.get("name", "") or ""):
            e["active"] = True
    return av, dropped


def to_bom_csv(entries: list) -> str:
    """规范化条目 -> 引擎 BOM CSV 文本（含类别/特性/参数/电气）。"""
    cols = ["设备类型", "品牌", "型号", "名称", "数量", "特性", "参数", "冗余",
            "处理器功能", "有源", "电气"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for e in entries:
        params = e.get("params", {}) or {}
        elec = e.get("electrical", {}) or {}
        w.writerow({
            "设备类型": e.get("category", ""),
            "品牌": e.get("brand", ""),
            "型号": e.get("model", ""),
            "名称": e.get("name", ""),
            "数量": e.get("quantity", 1),
            "特性": ";".join(e.get("features", []) or []),
            # 复杂参数（ports_override / slots 等 list/dict）整体转 JSON，
            # 否则 CSV 往返会退化成 Python repr 字符串，后端遍历时拿到字符而非字典。
            "参数": dump_params(params),
            "冗余": e.get("redundancy", "") or "",
            "处理器功能": e.get("proc_func", "") or "",
            "有源": "是" if e.get("active") else "",
            "电气": json.dumps(elec, ensure_ascii=False) if elec else "",
        })
    return buf.getvalue()
