"""设备规格库：从 YAML 加载，并按清单条目参数化展开为设备实例。"""
from __future__ import annotations
import os
import re
import yaml
from avcad.model.schema import (
    DeviceSpec, DeviceInstance, ConcretePort, Signal, Redundancy, Port,
)

# normpath 很关键：PyInstaller 打包后 __file__ 指向归档内的虚拟路径，
# 中间目录（如 avcad/model）在磁盘上并不存在，带 ".." 的路径会 ENOENT。
DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "device_specs"))

_CATEGORY_ORDER = [
    "SOURCE", "WIRELESS_MIC", "ANTENNA", "ANT_DIST", "WIRELESS_RX",
    "MIXER", "PROCESSOR", "SPEAKER_MGR", "AMP", "SPEAKER", "SWITCH",
]

_loaded = {}


def load_specs() -> dict:
    global _loaded
    if _loaded:
        return _loaded
    specs = {}
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.endswith((".yaml", ".yml")):
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                d = yaml.safe_load(f)
            spec = DeviceSpec(
                category=d["category"],
                name=d.get("name", d["category"]),
                redundancy_allowed=d.get("redundancy_allowed", False),
                proc_func=d.get("proc_func"),
                features_available=d.get("features_available", []),
                params=d.get("params", {}),
                ports_template=d.get("ports_template", []),
                electrical=d.get("electrical", {}),
                fixed=d.get("fixed", {}),
            )
            specs[d["category"]] = spec
    _loaded = specs
    return specs


def category_order() -> list:
    return list(_CATEGORY_ORDER)


def default_params(spec: DeviceSpec, overrides: dict) -> dict:
    p = {}
    for k, v in spec.params.items():
        p[k] = overrides.get(k, v.get("default", v.get("min", 1)))
    # 保留 overrides 中不在 spec 参数定义里的字段（如 slots / ports_override）
    for k, v in overrides.items():
        if k not in p:
            p[k] = v
    return p


def _norm_features(features) -> set:
    if not features:
        return set()
    if isinstance(features, (set, list)):
        return set(str(x).strip().lower() for x in features if x)
    return set(s.strip().lower() for s in re.split(r"[,;]", str(features)) if s.strip())


def expand_instance(spec: DeviceSpec, entry: dict, idx: int = 0) -> DeviceInstance:
    """把一条清单条目 + 规格展开为一个设备实例（含具体端口，坐标后置）。"""
    features = _norm_features(entry.get("features"))
    # 校验 feature 合法性
    valid = set(spec.features_available)
    features = {f for f in features if f in valid} | (
        set(str(x).strip().lower() for x in entry.get("features_default", [])) if False else set()
    )
    params = default_params(spec, entry.get("params", {}) or {})
    qty = int(entry.get("quantity", 1) or 1)
    redundancy = Redundancy(str(entry.get("redundancy", "NONE")).upper())
    active = bool(entry.get("active", spec.fixed.get("active", False)))

    uid = entry.get("uid") or f"{spec.category.lower()}_{idx+1}"
    inst = DeviceInstance(
        uid=uid, category=spec.category, name=entry.get("name") or spec.name,
        brand=entry.get("brand", ""), model=entry.get("model", ""),
        quantity=qty, features=features, params=params, redundancy=redundancy,
        pair=entry.get("pair"), active=active, spec_ref=spec.category,
        electrical={**spec.electrical, **(entry.get("electrical") or {})},
    )
    # 可扩展卡槽（如 YAMAHA HY/MY/RY），仅可视化，非对外接口
    inst.slots = list(params.get("slots", []))
    # 端口展开
    ports = []
    for t in spec.ports_template:
        if t.get("if_feature") and t["if_feature"] not in features:
            continue
        if "if_active" in t and bool(t["if_active"]) != active:
            continue
        # feature_ports=False 时跳过「依赖插卡/网络特性」的模板端口
        # （如 CS-R10 的 DANTE/CTRL 由 HY/MY 卡槽提供，未插卡则不画）
        if not params.get("feature_ports", True) and t.get("if_feature"):
            continue
        sig = Signal(t["signal"])
        side = t.get("side", "left")
        role = t.get("role", "io")
        cnt = int(t.get("count", 1))
        if t.get("count_from"):
            cnt = max(1, int(params.get(t["count_from"], cnt)))
        label = t.get("label", sig.value)
        for i in range(cnt):
            plabel = label + (str(i + 1) if cnt > 1 else "")
            ports.append(ConcretePort(
                id=f"{uid}:{t.get('name', label)}_{i+1}",
                uid=uid, side=side, signal=sig, label=plabel,
                index=i, role=role, air=bool(t.get("air", False)),
            ))
    # 设备特定端口覆盖（如 RMio64-D 这种特殊接口的转换器；空列表表示不画端口）
    if "ports_override" in params:
        override_ports = []
        for t in params["ports_override"]:
            cnt = int(t.get("count", 1))
            sig = Signal(t["signal"])
            side = t.get("side", "left")
            role = t.get("role", "io")
            base = t.get("name", t.get("label", "P"))
            label = t.get("label", base)
            for i in range(cnt):
                plabel = label + (str(i + 1) if cnt > 1 else "")
                override_ports.append(ConcretePort(
                    id=f"{uid}:{base}_{i+1}",
                    uid=uid, side=side, signal=sig, label=plabel,
                    index=i, role=role, air=False,
                ))
        ports = override_ports
    inst.ports = ports
    return inst


def build_instances(entries: list) -> list:
    """批量展开；同 category 多行 + quantity 展开为多个实例。"""
    specs = load_specs()
    instances = []
    counters = {}
    for e in entries:
        cat = str(e.get("category", "")).upper()
        spec = specs.get(cat)
        if spec is None:
            # 未知类型保留为裸实例交由校验模块报错
            counters[cat] = counters.get(cat, 0) + 1
            instances.append(DeviceInstance(
                uid=e.get("uid") or f"{cat.lower()}_{counters[cat]}",
                category=cat, name=e.get("name") or cat,
                brand=e.get("brand", ""), model=e.get("model", ""),
                quantity=int(e.get("quantity", 1) or 1),
                features=_norm_features(e.get("features")),
                params=e.get("params", {}) or {},
                redundancy=Redundancy(str(e.get("redundancy", "NONE")).upper()),
            ))
            continue
        qty = int(e.get("quantity", 1) or 1)
        base = counters.get(cat, 0)
        for k in range(qty):
            entry = dict(e)
            entry["uid"] = e.get("uid") or f"{cat.lower()}_{base + k + 1}"
            instances.append(expand_instance(spec, entry, base + k))
        counters[cat] = base + qty
    return instances
