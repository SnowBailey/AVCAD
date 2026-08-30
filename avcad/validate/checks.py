"""校验模块：出图前门禁。未连端口 / 通道不匹配 / 阻抗越限 / SPOF / 未知类型 / 无线分集缺失。"""
from __future__ import annotations
from collections import defaultdict
from avcad.model.schema import Issue, Signal, Redundancy

KNOWN = {"SOURCE", "WIRELESS_MIC", "ANTENNA", "ANT_DIST", "WIRELESS_RX",
         "MIXER", "PROCESSOR", "SPEAKER_MGR", "AMP", "SPEAKER", "SWITCH", "IO"}


def validate(project) -> list:
    issues = []

    # 未知类型
    for i in project.instances:
        if i.category not in KNOWN:
            issues.append(Issue("ERROR", "UNKNOWN_TYPE",
                                f"未知设备类型: {i.category} ({i.name})", i.uid))

    # 已用端口
    used = set()
    for c in project.connections:
        used.add((c.from_uid, c.from_port))
        used.add((c.to_uid, c.to_port))

    # 未连端口（非空中、非交换机）
    for i in project.instances:
        for p in i.ports:
            if p.air:
                continue
            if (i.uid, p.id) not in used:
                issues.append(Issue("INFO", "UNCONNECTED",
                                    f"{i.name} 端口 {p.label}({p.signal.value}) 未连接（余量）", i.uid))

    # 相邻阶段通道数匹配
    chain = project.chain
    by_stage = defaultdict(list)
    for i in project.instances:
        by_stage[i.stage].append(i)
    for a, b in zip(chain, chain[1:]):
        a_out = sum(1 for d in by_stage[a] for p in d.ports
                    if p.role == "out" and p.signal in (Signal.XLR, Signal.AES) and not p.air)
        b_in = sum(1 for d in by_stage[b] for p in d.ports
                   if p.role == "in" and p.signal in (Signal.XLR, Signal.AES) and not p.air)
        if a_out and b_in:
            if a_out > b_in:
                issues.append(Issue("INFO", "SPARE_OUT",
                                    f"{a}→{b}: 源输出 {a_out} > 目标输入 {b_in}（余量）", ""))
            elif b_in > a_out:
                issues.append(Issue("INFO", "UNMET_IN",
                                    f"{a}→{b}: 目标输入 {b_in} > 源输出 {a_out}（余量）", ""))

    # 阻抗/功率告警（来自连线阶段计算）：阻抗越限为 ERROR（不安全），
    # 功率裕量不足仅为 WARN（设计建议，不阻断出图）。
    for w in project.meta.get("amp_warnings", []):
        level = "ERROR" if ("越限" in w or "阻抗" in w) else "WARN"
        issues.append(Issue(level, "IMPEDANCE", w, ""))

    # 无线真分集：接收机需 >=2 路天线输入
    for i in project.instances:
        if i.category == "WIRELESS_RX":
            ant = [p for p in i.ports if p.signal == Signal.RF and p.role == "in"]
            if len(ant) < 2:
                issues.append(Issue("ERROR", "DIVERSITY",
                                    f"无线接收机 {i.name} 真分集需≥2路天线输入，当前{len(ant)}路", i.uid))

    # Dante 必须有交换机
    has_dante = any(p.signal == Signal.DANTE for i in project.instances for p in i.ports)
    if has_dante and not project.switches:
        issues.append(Issue("ERROR", "NO_SWITCH", "系统含 Dante 设备但未生成交换机", ""))

    # SPOF：主备设备共用同一交换机
    if project.switches and len(project.switches) == 1:
        redundant = any(i.redundancy != Redundancy.NONE for i in project.instances)
        backup_dante = any(i.is_backup and any(p.signal == Signal.DANTE for p in i.ports)
                            for i in project.instances)
        if redundant and backup_dante:
            issues.append(Issue("WARN", "SPOF",
                                "主备系统含 Dante 但仅 1 台交换机，建议冗余 Dante 用独立备交换机", ""))

    # 主备配对完整性
    for i in project.instances:
        if i.redundancy != Redundancy.NONE and i.pair:
            partner = next((x for x in project.instances if x.uid == i.pair), None)
            if partner is None:
                issues.append(Issue("ERROR", "PAIR_MISSING",
                                    f"{i.name} 主备配对缺失: {i.pair}", i.uid))
            elif partner.category != i.category:
                issues.append(Issue("ERROR", "PAIR_TYPE",
                                    f"{i.name} 与配对设备 {partner.name} 类型不一致", i.uid))

    project.issues = issues
    return issues
