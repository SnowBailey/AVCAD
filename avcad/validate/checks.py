"""校验模块：出图前门禁。未连端口 / 通道不匹配 / 阻抗越限 / SPOF / 未知类型 / 无线分集缺失。"""
from __future__ import annotations
from collections import defaultdict
from avcad.model.schema import Issue, Signal, Redundancy, redundancy_scope
from avcad.model.specs import load_specs


def known_categories() -> set:
    """已知设备类别 = 规格库里真正加载到的类别。

    ★ 此前这里是硬编码白名单，漏了 `MIC_HOST`，导致**每台会议主机都报一条
    「未知设备类型」的 ERROR**——而它的连线完全正确，纯属白名单没跟上规格库。
    改成从 `load_specs()` 动态取后，新增类别只要加一个 yaml 就自动被认识，
    不会再出现「类别加了却忘了同步某个白名单」这类漏报/误报。
    """
    return set(load_specs())


def validate(project) -> list:
    issues = []
    known = known_categories()

    # 未知类型
    for i in project.instances:
        if i.category not in known:
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
    # ★ 不变式守卫：build_project 保证「有 Dante 必有交换机」（清单没配就由
    #   _make_switches 造一台），所以正常流程走不到这里。保留它是为了在
    #   build_project 的交换机逻辑被改坏时立刻报错，而不是悄悄出一张没有
    #   交换机的 Dante 图。探针 scripts/probe_issue_coverage.py 里已登记为
    #   KNOWN_UNREACHABLE，别再当「疑似漏报」反复排查。
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

    # 冗余告警：清单标了冗余却没组成主备对（同类不足 2 台 / 超过 2 台）
    # ★ 此前这类情况静默失效，用户以为设了冗余、图上却毫无变化。
    for w in project.meta.get("redundancy_warnings", []):
        issues.append(Issue("WARN", "REDUNDANCY", w, ""))

    # 链路冗余要求 Dante 网络承载；无 Dante 时「双交换机」无从谈起
    if any(redundancy_scope(i.redundancy)["dual_switch"] for i in project.instances):
        if not any(p.signal == Signal.DANTE for i in project.instances for p in i.ports):
            issues.append(Issue("WARN", "LINK_BACKUP_NO_DANTE",
                                "配置了链路冗余，但系统无 Dante 设备，双交换机无从承载", ""))

    project.issues = issues
    return issues
