"""D14 线缆清册与 D03 端子表的投影与导出。

全部只读 `avcad.model.schema` 既有属性，不新增任何数据字段。
"""
from __future__ import annotations

import csv
import math
import os

from avcad.model.schema import (
    Project,
    DeviceInstance,
    ConcretePort,
    Connection,
    Signal,
    signal_layer,
)


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #
def _inst_map(project: Project) -> dict:
    """{uid: inst} 映射，覆盖清单设备与交换机。"""
    m: dict = {}
    for inst in (project.instances + project.switches):
        m[inst.uid] = inst
    return m


def _lookup_port(inst_map: dict, uid: str, port_id: str):
    """按 (uid, port.id) 取 ConcretePort；找不到返回 None（try/except 保护）。"""
    inst = inst_map.get(uid)
    if inst is None:
        return None
    try:
        return next(p for p in inst.ports if p.id == port_id)
    except StopIteration:
        return None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dev_label(inst_map: dict, uid: str) -> str:
    """设备展示名（name 优先，缺省回退 uid）。"""
    inst = inst_map.get(uid)
    if inst is not None and inst.name:
        return inst.name
    return uid


def _reverse_conn_map(project: Project) -> dict:
    """(uid, port_id) -> [对端 uid, ...] 反向查表（双向记录）。"""
    rev: dict = {}
    for conn in project.connections:
        rev.setdefault((conn.from_uid, conn.from_port), []).append(conn.to_uid)
        rev.setdefault((conn.to_uid, conn.to_port), []).append(conn.from_uid)
    return rev


# --------------------------------------------------------------------------- #
# D14 线缆清册
# --------------------------------------------------------------------------- #
def cable_schedule(project: Project) -> list:
    """每条 Connection 一行。空中接口（port.air）所在的连线整条跳过。

    行字段：
      seq, from_device, from_port, signal, to_device, to_port,
      layer, role, note, length_est_m
    length_est_m 为基于坐标的估算值（端口坐标优先，否则设备中心，皆无则 None）。
    """
    inst_map = _inst_map(project)
    rows: list = []
    seq = 0
    for conn in project.connections:
        from_port = _lookup_port(inst_map, conn.from_uid, conn.from_port)
        to_port = _lookup_port(inst_map, conn.to_uid, conn.to_port)
        # 任一端点端口存在且为空中接口 -> 跳过整条连线
        if from_port is not None and from_port.air:
            continue
        if to_port is not None and to_port.air:
            continue

        seq += 1
        from_inst = inst_map.get(conn.from_uid)
        to_inst = inst_map.get(conn.to_uid)

        from_device = (from_inst.name if from_inst and from_inst.name
                       else conn.from_uid)
        to_device = (to_inst.name if to_inst and to_inst.name
                     else conn.to_uid)
        from_port_disp = (from_port.label or from_port.id) if from_port else conn.from_port
        to_port_disp = (to_port.label or to_port.id) if to_port else conn.to_port

        length = _estimate_length(from_inst, to_inst, from_port, to_port)

        rows.append({
            "seq": seq,
            "from_device": from_device,
            "from_port": from_port_disp,
            "signal": conn.signal.value if conn.signal is not None else "",
            "to_device": to_device,
            "to_port": to_port_disp,
            "layer": signal_layer(conn.signal) if conn.signal is not None else "",
            "role": conn.role,
            "note": conn.note,
            "length_est_m": length,
        })
    return rows


def _estimate_length(from_inst, to_inst, from_port, to_port):
    """基于坐标估算线长（米）。端口坐标优先；否则设备中心坐标；皆无则 None。"""
    # 端口坐标
    if (from_port is not None and to_port is not None
            and _is_number(from_port.x) and _is_number(from_port.y)
            and _is_number(to_port.x) and _is_number(to_port.y)):
        return float(math.hypot(from_port.x - to_port.x, from_port.y - to_port.y))
    # 设备中心坐标
    if (from_inst is not None and to_inst is not None
            and _is_number(from_inst.x) and _is_number(from_inst.y)
            and _is_number(to_inst.x) and _is_number(to_inst.y)):
        return float(math.hypot(from_inst.x - to_inst.x, from_inst.y - to_inst.y))
    return None


# --------------------------------------------------------------------------- #
# D03 端子表
# --------------------------------------------------------------------------- #
def terminal_schedule(project: Project) -> dict:
    """按设备 uid 聚合端子表。

    结构：{uid: {"name":..., "category":..., "ports":[...]}}
    每个端口 dict：index, label, signal, role, connects_to（连接到的对端设备名，
    多连接以 ';' 分隔，无则 ''）。
    """
    inst_map = _inst_map(project)
    rev = _reverse_conn_map(project)
    result: dict = {}
    for inst in (project.instances + project.switches):
        ports = []
        for p in inst.ports:
            others = rev.get((inst.uid, p.id), [])
            connects = ";".join(_dev_label(inst_map, o) for o in others)
            ports.append({
                "index": p.index,
                "label": p.label,
                "signal": p.signal.value if p.signal is not None else "",
                "role": p.role,
                "connects_to": connects,
            })
        result[inst.uid] = {
            "name": inst.name,
            "category": inst.category,
            "ports": ports,
        }
    return result


def terminal_rows(project: Project) -> list:
    """把端子表拍平为每行一个 (设备, 端口) 的 List[dict]，便于导出。

    列：device, category, port_index, label, signal, role, connects_to
    """
    rows: list = []
    for uid, entry in terminal_schedule(project).items():
        for p in entry["ports"]:
            rows.append({
                "device": entry["name"],
                "category": entry["category"],
                "port_index": p["index"],
                "label": p["label"],
                "signal": p["signal"],
                "role": p["role"],
                "connects_to": p["connects_to"],
            })
    return rows


# --------------------------------------------------------------------------- #
# 通用导出（utf-8）
# --------------------------------------------------------------------------- #
def _fieldnames(rows: list) -> list:
    cols: list = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    return cols


def export_csv(rows: list, path) -> str:
    """把 List[dict] 写成 utf-8 CSV，返回路径。"""
    path = str(path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    cols = _fieldnames(rows)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def export_markdown(rows: list, path) -> str:
    """把 List[dict] 写成 utf-8 Markdown 表格，返回路径。"""
    path = str(path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    cols = _fieldnames(rows)
    lines: list = []
    if cols:
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    else:
        lines.append("(empty)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
