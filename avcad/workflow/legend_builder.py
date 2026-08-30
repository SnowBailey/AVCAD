"""图例定义器（清单驱动工作流 步骤③）。

- from_instance(): 从已展开的设备实例提取「默认图例」（按信号/角色/朝向/标签分组并计数量）。
- ensure(): 缓存命中则直接用用户确认过的图例；未命中则自动以默认图例落库（首次遇到即缓存，
  下次同型号自动回填，符合步骤④语义）。
- replace_ports()/add_slot()/remove_slot(): 供 UI 定义/修改图例（端口数量、增删端口、卡槽）。
所有图例最终经 LegendStore 持久化（原子写）。
"""
from __future__ import annotations
import re
from typing import List, Optional

from avcad.workflow.legend_store import LegendStore, Legend, LegendPort


def _base_label(label: str, signal: str) -> str:
    base = re.sub(r"\d+$", "", label or "")
    return base or signal


def from_instance(inst) -> Legend:
    """从设备实例的端口提取默认图例（同 信号/角色/朝向/基标签 的端口合并计数）。"""
    groups: dict = {}
    order: List[tuple] = []
    for p in inst.ports:
        base = _base_label(p.label, p.signal.value)
        key = (p.signal.value, p.role, p.side, base, p.air)
        if key not in groups:
            groups[key] = 0
            order.append(key)
        groups[key] += 1
    ports = [
        LegendPort(signal=k[0], role=k[1], side=k[2], count=groups[k], label=k[3], air=k[4])
        for k in order
    ]
    return Legend(
        brand=inst.brand, model=inst.model, category=inst.category,
        ports=ports, slots=list(inst.slots),
    )


def ensure(inst, store: LegendStore) -> Legend:
    """缓存命中返回已确认图例；否则以默认图例落库并返回（自动缓存）。"""
    cached = store.get(inst.brand, inst.model)
    if cached is not None:
        return cached
    lg = from_instance(inst)
    store.put(lg)
    store.save()
    return lg


def replace_ports(legend: Legend, port_defs: List[dict]) -> Legend:
    """用 port_defs 整体替换图例端口（UI 定义/修改端口数量与朝向）。"""
    legend.ports = [
        LegendPort(
            signal=d["signal"], role=d.get("role", "io"), side=d.get("side", "right"),
            count=int(d.get("count", 1)), label=d.get("label", ""), air=bool(d.get("air", False)),
        )
        for d in port_defs
    ]
    return legend


def add_slot(legend: Legend, slot: dict) -> Legend:
    legend.slots.append(slot)
    return legend


def remove_slot(legend: Legend, index: int) -> Legend:
    if 0 <= index < len(legend.slots):
        legend.slots.pop(index)
    return legend
