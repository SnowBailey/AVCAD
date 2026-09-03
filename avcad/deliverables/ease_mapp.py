"""AVCAD 出图 ↔ EASE / MAPP 对接导出（doc 35 Phase A）。

★ 零外部依赖，纯 stdlib。把 AVCAD 系统图里的扬声器实例导出为 EASE/MAPP 可导入的
  - speakers.csv   （id, model, x, y, z, aim_az, aim_el, rot_z, category, power_w, sensitivity_db）
  - audience.csv   （聆听点；AVCAD 当前不建模观众区，导出表头 + 可选点，缺失时仅表头）
  - project.json   （项目元信息 + 扬声器清单 + 坐标变换参数 + 未建模项说明）
  - geometry.dxf   （可选；由标准 DXF 导出复用，单位/轴向见 doc 35 Phase B）

★ 坐标归一化（AVCAD 内部布局坐标 → EASE 舞台中心 mm）：
  - 原点：project.meta["stage_center"] = [cx, cy]（米）；缺省取所有实例位置质心。
  - 比例：project.meta["ease_scale"]（内部单位→mm 的倍数），缺省 1.0。
  - 高度 z：实例 .z 单位为米，导出时 ×1000 转 mm，与 x/y 单位对齐。
  - 注：AVCAD 布局为 2D，z 默认 0（地面层）；指向角 aim_az/aim_el/rot_z 由用户在
    第③步图例/实例上填写（schema.DeviceInstance 新增字段），未填即 0。

★ 数据先于规则：导出只读取现有字段（active / category / z / aim_* / electrical / params），
不新增任何校验字段。
"""
from __future__ import annotations

import csv
import json
import os
from typing import Optional

from avcad.model.schema import DeviceInstance, Project

SPEAKER_CSV_HEADER = [
    "id", "model", "x", "y", "z",
    "aim_az", "aim_el", "rot_z",
    "category", "power_w", "sensitivity_db",
]
AUDIENCE_CSV_HEADER = ["id", "x", "y", "z"]


def _stage_center(project: Project) -> tuple:
    """返回舞台中心 (cx, cy)（内部单位）。优先 meta，否则取实例位置质心。"""
    sc = project.meta.get("stage_center")
    if isinstance(sc, (list, tuple)) and len(sc) >= 2:
        return float(sc[0]), float(sc[1])
    insts = project.instances
    if not insts:
        return 0.0, 0.0
    cx = sum(i.x for i in insts) / len(insts)
    cy = sum(i.y for i in insts) / len(insts)
    return cx, cy


def _ease_scale(project: Project) -> float:
    try:
        return float(project.meta.get("ease_scale", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _norm_point(i: DeviceInstance, project: Project) -> tuple:
    """把实例内部坐标转 EASE 舞台中心 mm。"""
    cx, cy = _stage_center(project)
    s = _ease_scale(project)
    x_mm = (i.x - cx) * s
    y_mm = (i.y - cy) * s
    z_mm = i.z * 1000.0  # 米 → mm
    return x_mm, y_mm, z_mm


def _is_speaker(i: DeviceInstance) -> bool:
    return i.category == "SPEAKER" or i.active


def speakers_rows(project: Project) -> list:
    """生成 speakers.csv 的数据行（不含表头）。"""
    rows = []
    for i in project.instances:
        if not _is_speaker(i):
            continue
        x_mm, y_mm, z_mm = _norm_point(i, project)
        power_w = i.electrical.get("power_w", "") if i.electrical else i.params.get("power_w", "")
        sens = i.params.get("sensitivity_db", "")
        rows.append([
            i.uid, i.model or i.name,
            f"{x_mm:.1f}", f"{y_mm:.1f}", f"{z_mm:.1f}",
            f"{i.aim_az:.1f}", f"{i.aim_el:.1f}", f"{i.rot_z:.1f}",
            i.category, power_w, sens,
        ])
    return rows


def audience_rows(project: Project) -> list:
    """生成 audience.csv 的数据行。AVCAD 不建模观众区，仅导出 meta 提供的点。"""
    pts = project.meta.get("audience_positions") or []
    rows = []
    for idx, p in enumerate(pts, 1):
        try:
            x = float(p.get("x", 0.0))
            y = float(p.get("y", 0.0))
            z = float(p.get("z", 1.2))  # 听音高度默认 1.2 m
        except (TypeError, ValueError):
            continue
        rows.append([f"A{idx}", f"{x:.1f}", f"{y:.1f}", f"{z * 1000:.1f}"])
    return rows


def build_project_json(project: Project, speakers: list, audience: list) -> dict:
    """生成 project.json 内容（含坐标变换参数 + 未建模项说明）。"""
    cx, cy = _stage_center(project)
    return {
        "project": project.name,
        "schema": "avcad-ease-mapp/v1",
        "coordinate_transform": {
            "origin": [cx, cy],
            "scale_internal_to_mm": _ease_scale(project),
            "z_unit": "m->mm (x1000)",
            "note": "AVCAD 布局为 2D，z 默认 0（地面层）；指向角来自实例字段，未填即 0。",
        },
        "speakers": [
            {
                "id": r[0], "model": r[1],
                "x": r[2], "y": r[3], "z": r[4],
                "aim_az": r[5], "aim_el": r[6], "rot_z": r[7],
                "category": r[8], "power_w": r[9], "sensitivity_db": r[10],
            }
            for r in speakers
        ],
        "audience": [{"id": r[0], "x": r[1], "y": r[2], "z": r[3]} for r in audience],
        "unmodeled_notes": [
            "AVCAD 当前不建模观众区（audience.csv 仅含 meta.audience_positions，缺省为空）。",
            "geometry.dxf 由标准 DXF 导出复用（2D，单位/轴向归一化见 doc 35 Phase B）。",
        ],
    }


def export_ease_package(project: Project, out_dir: str,
                        dxf_bytes: Optional[bytes] = None) -> dict:
    """把 EASE/MAPP 对接包写到 out_dir，返回 {文件: 路径} 与统计。

    dxf_bytes 可选：传入则由标准 DXF 导出产出的字节，落盘为 geometry.dxf。
    """
    os.makedirs(out_dir, exist_ok=True)

    speakers = speakers_rows(project)
    audience = audience_rows(project)

    spk_path = os.path.join(out_dir, "speakers.csv")
    aud_path = os.path.join(out_dir, "audience.csv")
    prj_path = os.path.join(out_dir, "project.json")

    with open(spk_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SPEAKER_CSV_HEADER)
        w.writerows(speakers)

    with open(aud_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(AUDIENCE_CSV_HEADER)
        w.writerows(audience)

    with open(prj_path, "w", encoding="utf-8") as f:
        json.dump(build_project_json(project, speakers, audience), f,
                  ensure_ascii=False, indent=2)

    files = {"speakers.csv": spk_path, "audience.csv": aud_path, "project.json": prj_path}
    if dxf_bytes:
        dxf_path = os.path.join(out_dir, "geometry.dxf")
        with open(dxf_path, "wb") as f:
            f.write(dxf_bytes)
        files["geometry.dxf"] = dxf_path

    return {
        "files": files,
        "speaker_count": len(speakers),
        "audience_count": len(audience),
    }
