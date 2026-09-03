"""校验模块：出图前门禁。未连端口 / 通道不匹配 / 阻抗越限 / SPOF（主备共用交换机 · 链路单点处理器）/ 未知类型 / 无线分集缺失。"""
from __future__ import annotations
from collections import defaultdict
from avcad.model.schema import (Issue, Signal, Redundancy, redundancy_scope,
                                VALID_SIDES, VALID_ROLES)
from avcad.model.specs import load_specs
from avcad.validate.standards_gb55024 import check_standards


def known_categories() -> set:
    """已知设备类别 = 规格库里真正加载到的类别。

    ★ 此前这里是硬编码白名单，漏了 `MIC_HOST`，导致**每台会议主机都报一条
    「未知设备类型」的 ERROR**——而它的连线完全正确，纯属白名单没跟上规格库。
    改成从 `load_specs()` 动态取后，新增类别只要加一个 yaml 就自动被认识，
    不会再出现「类别加了却忘了同步某个白名单」这类漏报/误报。
    """
    return set(load_specs())


def dante_primary_backup_switch_overlap(project) -> set:
    """返回主备 Dante 设备共用的交换机 uid 集合（空 = 无 SPOF）。

    ★ C1 实证缺陷修复：旧逻辑用 `len(project.switches) == 1` 近似，
    漏报「2 台交换机却把主备都接到同一台」的情形（主备形同虚设）。
    这里用语义判据 S_p ∩ S_b ≠ ∅：主用（非备份）Dante 设备所接交换机集合
    与 备用（is_backup）Dante 设备所接交换机集合 有交集 → 该交换机是单点故障。
    """
    if not project.switches:
        return set()
    by_uid = {i.uid: i for i in project.instances + project.switches}
    dev_sw: dict = {}
    for c in project.connections:
        if c.signal != Signal.DANTE:
            continue
        for end, other in ((c.from_uid, c.to_uid), (c.to_uid, c.from_uid)):
            d = by_uid.get(end)
            if d is None or d.category == "SWITCH":
                continue
            o = by_uid.get(other)
            if o is not None and o.category == "SWITCH":
                dev_sw.setdefault(end, set()).add(other)
    prim_devs = [u for u in dev_sw if not by_uid[u].is_backup]
    bak_devs = [u for u in dev_sw if by_uid[u].is_backup]
    if not prim_devs or not bak_devs:
        return set()
    prim_sw = set().union(*[dev_sw[u] for u in prim_devs])
    bak_sw = set().union(*[dev_sw[u] for u in bak_devs])
    return prim_sw & bak_sw


# 音频域信号：用于「信号链路单点」的图可达性判定。电源/控制/天线射频不在内
# （它们不构成「音频链路」，SPEAKER 在域内——它代表音频到达终端负载）。
_AUDIO_SIGNALS = {
    Signal.XLR, Signal.AES, Signal.DANTE, Signal.TRS, Signal.OPTICAL,
    Signal.USB, Signal.CONF, Signal.LINK, Signal.WCLK, Signal.SPEAKER,
}


def dsp_single_points_of_failure(project) -> list:
    """返回信号链路上的单点处理器（割点）。

    ★ 纯图论判据，不依赖任何新字段：在音频信号无向图上对每台 PROCESSOR
       做割点判定——移除它后若原本连通的两个节点不再互通，则它是单点故障。

    天然避误报的三类情形（都不构成割点）：
      - 叶子处理器（只进不出 / 只出不进，度数 < 2）；
      - 挂在网络交换机上的星形拓扑（邻居只有交换机一个节点）；
      - 已配 PROCESSOR_BACKUP / 有主备配对的处理器（已被冗余覆盖）。
    """
    by_uid = {i.uid: i for i in project.instances + project.switches}
    adj = defaultdict(set)
    for c in project.connections:
        if c.signal not in _AUDIO_SIGNALS:
            continue
        adj[c.from_uid].add(c.to_uid)
        adj[c.to_uid].add(c.from_uid)

    def is_cut_vertex(v):
        neigh = adj.get(v, set())
        if len(neigh) < 2:
            return False
        start = next(iter(neigh))
        seen = {start}
        stack = [start]
        while stack:
            n = stack.pop()
            for m in adj.get(n, ()):
                if m == v:
                    continue
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        # 所有其它邻居都能从 start 不经 v 到达 → 非割点
        return not neigh.issubset(seen)

    out = []
    for uid, d in by_uid.items():
        if d is None or d.category != "PROCESSOR":
            continue
        if d.redundancy != Redundancy.NONE or d.pair or d.is_backup:
            continue
        if is_cut_vertex(uid):
            out.append(d)
    return out


def dante_device_to_device_links(project) -> list:
    """返回未经过交换机中转的 Dante 设备直连。

    ★ 落线即挡（P1/P2 项）的第一刀，纯图遍历、零新字段：AVCAD 拓扑约定
    「Dante 一律经交换机」（主备也各自走一台交换机），因此两台**非交换机**
    设备之间直接存在 DANTE 连接属可疑拓扑，需人工确认。正常流程 device↔switch
    不触发。
    """
    by_uid = {i.uid: i for i in project.instances + project.switches}
    bad = []
    for c in project.connections:
        if c.signal != Signal.DANTE:
            continue
        a = by_uid.get(c.from_uid)
        b = by_uid.get(c.to_uid)
        if a is None or b is None:
            continue
        if a.category != "SWITCH" and b.category != "SWITCH":
            bad.append(c)
    return bad


def dante_components_without_switch(project) -> list:
    """返回全部 DANTE 连接子图中、不含任何交换机的连通分量里的设备 uid。

    ★ 落线即挡第二刀，纯图遍历、零新字段：完备的 Dante 部署里，每台 Dante 设备
    必有交换机落在同一连通分量内（经交换机中转）。若某连通分量一个交换机都没有，
    说明这批设备全靠 device↔device 直连组网，属错误拓扑。正常 device↔switch 拓扑
    的分量都含交换机，故零误报。仅统计「确有 DANTE 边」的设备（Dante 端口留空未用
    的不算）。
    """
    by_uid = {i.uid: i for i in project.instances + project.switches}
    adj = {}
    nodes = set()
    for c in project.connections:
        if c.signal != Signal.DANTE:
            continue
        a = by_uid.get(c.from_uid)
        b = by_uid.get(c.to_uid)
        if a is None or b is None:
            continue
        nodes.add(c.from_uid)
        nodes.add(c.to_uid)
        adj.setdefault(c.from_uid, []).append(c.to_uid)
        adj.setdefault(c.to_uid, []).append(c.from_uid)
    bad = []
    seen = set()
    for start in nodes:
        if start in seen:
            continue
        stack = [start]
        comp = []
        seen.add(start)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        if not any(by_uid[n].category == "SWITCH" for n in comp):
            bad.extend(comp)
    return bad


def _has_analog_io(dev) -> bool:
    """设备是否有 XLR/AES 端口（failover 备份线物理上可画）。"""
    return any(p.signal in (Signal.XLR, Signal.AES) and not p.air
               for p in dev.ports)


def dante_devices_without_connection(project) -> list:
    """返回「有 Dante 端口却零 Dante 连线」的设备（Dante 网络盲区）。

    ★ S8 修复：DANTE_NO_SWITCH_HOP / DANTE_NO_NETWORK 只遍历确有 DANTE 边的设备，
    若某台 Dante 设备「忘了接 Dante」（有端口却一条 DANTE 连接都没有），它不在任何
    DANTE 边里，上述两刀完全看不到——等于睁眼瞎。本函数补这个盲区：只要系统里
    已有其它设备通过 DANTE 连上了网络，任何**有 Dante 端口的非交换机设备**自身
    却零 DANTE 连接，即属「漏接 Dante」，提示人工确认。零误报：整个系统一条 DANTE
    连线都没有时不触发（那属于全模拟或整体未接，不在本刀范围）。
    """
    dante_uids = set()
    for c in project.connections:
        if c.signal == Signal.DANTE:
            dante_uids.add(c.from_uid)
            dante_uids.add(c.to_uid)
    if not dante_uids:
        return []
    bad = []
    for i in project.instances:
        if i.category == "SWITCH":
            continue
        if any(p.signal == Signal.DANTE and not p.air for p in i.ports) \
                and i.uid not in dante_uids:
            bad.append(i)
    return bad


def redundant_pair_without_failover(project) -> list:
    """返回「声明了主备冗余却没画出 failover 线」的设备（静默假冗余）。

    ★ S9 修复：SPOF_DSP_SINGLE 遇 `pair` 即排除，PAIR_* 只查 uid/类别不查是否真有
    `role="backup"` 连线。若清单声明了主备（`redundancy` 需 failover_link 且设了
    `pair`），但 `_failover` 因故没画出备份线（图上零冗余），此前零报错。本函数补盲区：
    需 failover_link 的冗余级别（DEVICE/PROCESSOR/FULL_CHAIN）且两端都有 XLR/AES
    能力时，必须有 `role="backup"` 连线连接这对设备，否则告警。
    LINK_BACKUP（failover_link=False，冗余在网络层）本就不画 failover 线，不触发；
    纯 Dante 设备（无 XLR/AES 口，failover 线物理画不出）也不触发，避免误报。
    """
    linked = {(min(c.from_uid, c.to_uid), max(c.from_uid, c.to_uid))
              for c in project.connections if c.role == "backup"}
    bad = []
    for i in project.instances:
        if not i.pair or i.redundancy == Redundancy.NONE:
            continue
        if i.is_backup:          # 每对只由主用侧报一次，避免主备各报一条
            continue
        if not redundancy_scope(i.redundancy)["failover_link"]:
            continue
        partner = next((x for x in project.instances if x.uid == i.pair), None)
        if partner is None:
            continue
        if not (_has_analog_io(i) and _has_analog_io(partner)):
            continue
        key = (min(i.uid, partner.uid), max(i.uid, partner.uid))
        if key not in linked:
            bad.append(i)
    return bad


def _truthy(v) -> bool:
    """把 catalog 里可能写成字符串的布尔键归一成 bool。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是", "需要")


def _audio_upstream(project, start_uid, adj_up=None) -> set:
    """沿音频信号从 start 向上游(出→入反向)可达的设备 uid 集合。

    用于「补声/延时音箱的上游处理器是否带某能力」「定压音箱挂在哪台功放」
    这类拓扑判据。adj_up 可外部传入以复用（Tier 3 一次性构建）。
    """
    by_uid = {i.uid: i for i in project.instances + project.switches}
    if adj_up is None:
        adj_up = defaultdict(set)
        for c in project.connections:
            if c.signal in _AUDIO_SIGNALS:
                adj_up[c.to_uid].add(c.from_uid)
    seen, stack = set(), [start_uid]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj_up.get(u, ()))
    return seen


def _upstream_has_feature(project, start_uid, feature, adj_up=None) -> bool:
    """start 的上游音频链路里是否存在某 category 且带指定 features 的设备。"""
    by_uid = {i.uid: i for i in project.instances + project.switches}
    for u in _audio_upstream(project, start_uid, adj_up):
        d = by_uid.get(u)
        if d is not None and d.category == "PROCESSOR" and feature in d.features:
            return True
    return False


def _as_issue(item, default_code, default_level="WARN"):
    """把 meta 里的告警条目规整成 Issue。

    兼容 A 批前置改造后的 (level, code, msg) 三元组与旧的纯字符串兜底：
    三元组直出（计算层已定级），纯字符串退回中文子串嗅探（仅兜底，不应再出现）。
    """
    if isinstance(item, tuple) and len(item) == 3:
        lvl, code, msg = item
        return Issue(lvl, code, msg, "")
    msg = str(item)
    lvl = default_level
    if "越限" in msg or "阻抗" in msg:
        lvl = "ERROR"
    return Issue(lvl, default_code, msg, "")


def validate(project) -> list:
    issues = []
    known = known_categories()

    # 未知类型
    for i in project.instances:
        if i.category not in known:
            issues.append(Issue("ERROR", "UNKNOWN_TYPE",
                                f"未知设备类型: {i.category} ({i.name})", i.uid))

    # 端口方向 / 进出角色合法性
    # ★ 不变式守卫：这两个值只来自规格模板与主库 ports_override（前端下拉已
    #   受限），正常数据零命中。写错的后果不是崩溃而是静默失效——side 错会让
    #   端口坐标停在 (0,0) 飞出图纸，role 错会让端口不参与任何配对、图上只
    #   显示成「余量未连」。保留它以便在数据被写坏时立刻定位，而不是靠肉眼看图。
    for i in project.instances + project.switches:
        for p in i.ports:
            if p.side not in VALID_SIDES:
                issues.append(Issue("ERROR", "PORT_SIDE",
                                    f"{i.name} 端口 {p.label} 方向非法: {p.side!r}"
                                    f"（应为 {'/'.join(VALID_SIDES)}）", i.uid))
            if p.role not in VALID_ROLES:
                issues.append(Issue("ERROR", "PORT_ROLE",
                                    f"{i.name} 端口 {p.label} 进出角色非法: {p.role!r}"
                                    f"（应为 {'/'.join(VALID_ROLES)}）", i.uid))

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

    # 孤立设备：整台设备**一个端口都没连上**
    # ★ 与 UNCONNECTED 的区别：那条是「端口有余量」（INFO，正常），
    #   这条是「这台设备根本没进系统」——出图后是个孤立方块。
    #
    #   真实触发场景：配单里出现主库没有的新型号时，它会被兜底成 IO 类别
    #   （importers.py:409），IO 又被 assign_stages 扔到 SIDE 层不参与主链路
    #   配对（chain.py:137），于是连线数为 0。此前**完全静默**——既没有
    #   ERROR 也没有 WARN，混在几十条 INFO:UNCONNECTED 里看不出来。
    #
    #   ★ 按 (名称, 型号, 类别) **聚合**成一条，不逐台报：
    #     B-EAW4 有 24 台音箱因为清单缺前端设备而全孤——逐台报 24 条是噪音，
    #     聚合成 1 条「×24」既准确又可读。新型号那种 1 台的则原样单独一条。
    #
    #   豁免：WIRELESS_MIC（无线发射端设计上不产生线缆连接，只画本体与空中 RF 口）。
    ORPHAN_EXEMPT = ("WIRELESS_MIC",)
    orphans = [i for i in project.instances
               if i.category not in ORPHAN_EXEMPT
               and not any((i.uid, p.id) in used for p in i.ports)]
    if orphans:
        # 清单里连一台前端设备都没有时，孤立是清单不完整所致、非程序缺陷，
        # 措辞要跟「某台设备没接上」区分开（与 validate_projects.py 同一口径）
        front = {"AMP", "MIXER", "PROCESSOR", "SPEAKER_MGR", "SOURCE",
                 "WIRELESS_RX", "MIC_HOST", "SWITCH", "IO"}
        has_front = any(i.category in front for i in project.instances)
        head = "清单只含后端设备（缺调音台/处理器/功放等前端），" if not has_front else ""
        groups: dict = {}
        for i in orphans:
            k = (i.name, i.model, i.category)
            g = groups.setdefault(k, {"n": 0, "ports": len(i.ports), "uid": i.uid})
            g["n"] += 1
        for (nm, mdl, cat), g in groups.items():
            n = f"×{g['n']} " if g["n"] > 1 else ""
            issues.append(Issue(
                "WARN", "ISOLATED_DEVICE",
                f"{head}{nm}（{mdl or cat}）{n}未接入系统："
                f"{g['ports']} 个端口全部未连接", g["uid"]))

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

    # 阻抗/功率告警（来自连线阶段计算）：计算层已直出 (level, code, msg) 三元组
    # 透传，不再靠中文子串嗅探 ERROR/WARN —— 否则 CV_* 定压码因文案含"阻抗"会被
    # 误升 ERROR（A 批前置改造，findings/37 §4.1）。旧版纯字符串兜底由 _as_issue 处理。
    # 已知码：IMPEDANCE（阻抗越限 ERROR）、AMP_UNDERPOWERED（功率裕量不足 WARN，
    # 计算层独立成码，避免被阻抗 ERROR 文案吞掉或正常阻抗下漏报）。
    for w in project.meta.get("amp_warnings", []):
        if isinstance(w, tuple) and len(w) == 3 and w[1] == "AMP_UNDERPOWERED":
            issues.append(Issue("WARN", "AMP_UNDERPOWERED", w[2], ""))
        else:
            issues.append(_as_issue(w, "IMPEDANCE"))

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

    # SPOF：主备 Dante 设备是否共享同一台交换机（集合判据，替代旧的 len==1 近似）
    # ★ C1 实证缺陷：旧逻辑只看「交换机台数==1」，漏报「2 台交换机却把主备都接到
    #   同一台」的情况（主备形同虚设）。改用语义判据 S_p ∩ S_b ≠ ∅：
    #   主用设备所接交换机集合 与 备用设备所接交换机集合 有交集 → 单点故障。
    shared = dante_primary_backup_switch_overlap(project)
    if shared:
        by_uid = {i.uid: i for i in project.instances + project.switches}
        names = ", ".join(sorted(by_uid[s].name or s for s in shared))
        issues.append(Issue("ERROR", "SPOF_NET_SHARED_SWITCH",
            f"主备 Dante 设备共用交换机 [{names}]，该交换机故障将致主备同时失效（单点故障）", ""))
    elif project.switches and len(project.switches) < 2 and \
            any(redundancy_scope(i.redundancy)["dual_switch"] for i in project.instances):
        # 有「需双交换机」的冗余级别（DEVICE/FULL_CHAIN/ LINK 类网络冗余）却只 1 台交换机：
        # 克隆/生成未生效的安全网。★ S10 修复：原判据 `is_backup` 会把 PROCESSOR_BACKUP
        # （dual_switch=False，冗余在处理器设备级、不要求网络双交换机）误报成「网络冗余未建立」，
        # 改用冗余级别 scope 的 dual_switch 标志精准判定，消除设备级误报。
        issues.append(Issue("WARN", "SPOF_NET_NO_DUAL_SWITCH",
            "系统含需双交换机承载的冗余级别但仅 1 台 Dante 交换机，链路冗余未真正建立", ""))

    # SPOF：信号链路单点处理器（图可达性，割点判定）
    # ★ 延展 SPOF_NET_* 的「单点」思路到音频链路内部：某台 PROCESSOR 若位于
    #   一条必须经过的信号链路上（移除即断链），它本身就是单点故障。纯图论判据，
    #   不引入新字段；叶子/星形拓扑与已配 PROCESSOR_BACKUP 的处理器不会误报。
    #   级别 WARN（设计提示，非硬错误；用户可在知晓风险下选择不冗余）。
    for d in dsp_single_points_of_failure(project):
        issues.append(Issue("WARN", "SPOF_DSP_SINGLE",
            f"处理器 {d.name} 处于信号链路单点（移除即断链），"
            f"建议配置 PROCESSOR_BACKUP 冗余", d.uid))

    # 落线即挡（第一刀）：Dante 必须过交换机
    # ★ 纯图遍历、零新字段。拓扑约定「Dante 一律经交换机」（主备也各自走一台交换机），
    #   两台**非交换机**设备间直接存在 DANTE 连接属可疑拓扑，需人工确认。正常流程
    #   device↔switch 不触发，故零误报风险。级别 WARN（设计提示，非硬错误）。
    for c in dante_device_to_device_links(project):
        issues.append(Issue("WARN", "DANTE_NO_SWITCH_HOP",
            f"Dante 链路 {c.from_uid}→{c.to_uid} 未经过交换机中转，"
            f"请确认是否符合『Dante 一律经交换机』约定", c.to_uid))

    # 落线即挡（第二刀）：Dante 子图必须含交换机
    # ★ 第一刀是「逐条连接」检查；本刀是「连通分量」视角：多台 Dante 设备互相
    #   device↔device 直连、整个分量一台交换机都没有 = 网络根本没搭起来。纯图遍历、
    #   零新字段；只统计确有 DANTE 边的设备，零误报。级别 WARN。
    for uid in dante_components_without_switch(project):
        issues.append(Issue("WARN", "DANTE_NO_NETWORK",
            f"Dante 设备 {uid} 所在的 Dante 网络子图不含任何交换机，"
            f"疑似 device↔device 直连组网，请确认拓扑", uid))

    # S8：Dante 网络盲区（有 Dante 端口却零 Dante 连线）
    # ★ 纯图遍历、零新字段。DANTE_NO_SWITCH_HOP / DANTE_NO_NETWORK 只遍历确有
    #   DANTE 边的设备，若某台 Dante 设备「忘了接 Dante」，它不在任何 DANTE 边里，
    #   上述两刀完全看不到。本刀补盲区：系统里已有其它设备通过 DANTE 连上网络时，
    #   任何有 Dante 端口的非交换机设备自身却零 DANTE 连接，即属「漏接 Dante」。
    #   整个系统一条 DANTE 连线都没有时不触发（那属于全模拟或整体未接）。
    for i in dante_devices_without_connection(project):
        issues.append(Issue("WARN", "DANTE_NO_CONNECTION",
            f"{i.name} 含 Dante 端口但未接入任何 Dante 连接，疑似漏接 Dante 网络", i.uid))

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

    # S9：声明了主备冗余却没画出 failover 线（静默假冗余）
    # ★ 纯图遍历、零新字段。SPOF_DSP_SINGLE 遇 pair 即排除，PAIR_* 只查 uid/类别，
    #   若 _failover 因故没画出备份线（图上零冗余），此前零报错。本刀补盲区：
    #   需 failover_link 的冗余级别且两端都有 XLR/AES 能力时必须有 role="backup"
    #   连线，否则告警。LINK_BACKUP（failover_link=False）与纯 Dante 设备不触发。
    for i in redundant_pair_without_failover(project):
        issues.append(Issue("WARN", "REDUNDANT_NO_FAILOVER",
            f"{i.name} 声明了主备冗余但未画出 failover 备份线，图上零冗余（静默假冗余）", i.uid))

    # 冗余告警：清单标了冗余却没组成主备对（同类不足 2 台 / 超过 2 台）
    # ★ 此前这类情况静默失效，用户以为设了冗余、图上却毫无变化。
    for w in project.meta.get("redundancy_warnings", []):
        issues.append(Issue("WARN", "REDUNDANCY", w, ""))

    # 链路冗余要求 Dante 网络承载；无 Dante 时「双交换机」无从谈起
    if any(redundancy_scope(i.redundancy)["dual_switch"] for i in project.instances):
        if not any(p.signal == Signal.DANTE for i in project.instances for p in i.ports):
            issues.append(Issue("WARN", "LINK_BACKUP_NO_DANTE",
                                "配置了链路冗余，但系统无 Dante 设备，双交换机无从承载", ""))

    # GB 55024-2022 全文强制项 → 设计依据提醒（零新字段，纯数据驱动）
    issues.extend(check_standards(project))

    # ── Tier 2 音频理解优化（电平 / 幻象供电 / 有源误接）─────────────────────
    # 设计原则：只靠「连接 signal + 设备 category + 现成 active 标志」推断，
    # 不读主库量化数据、不新增任何字段——避免误报。电平量化表(LEVEL_MISMATCH
    # 的 dB 比较) 属 02 第三批，需先补数据层，本批次不碰。
    by_dev = {i.uid: i for i in project.instances + project.switches}

    # ACTIVE_ON_AMP_OUT（ERROR）：有源音箱（active=True）挂在功放/音响管理器的
    # 扬声器线缆(SPEAKER)输出端——会烧毁音箱。零误报：无源音箱接 SPEAKER 缆是
    # 正常，有源接 SPEAKER 缆=误接；active 标志已精确区分两者。
    for c in project.connections:
        if c.signal != Signal.SPEAKER:
            continue
        td = by_dev.get(c.to_uid)
        if td is None or td.category != "SPEAKER" or not td.active:
            continue
        fd = by_dev.get(c.from_uid)
        src = fd.name if fd else c.from_uid
        issues.append(Issue("ERROR", "ACTIVE_ON_AMP_OUT",
            f"{td.name} 是有源音箱，却接到 {src} 的扬声器线缆输出"
            f"（有源音箱应接线路电平，不可接功放/音响管理器扬声器输出）", td.uid))

    # LEVEL_DOMAIN（ERROR）：扬声器线缆(SPEAKER)的两端必须分别是功放/音响管理器
    # (源) 与 无源音箱(汇)。任何「扬声器电平 ↔ 线路/麦克风电平」的硬跨档都会烧前端
    # 或无声。数字/控制/电源信号不走电平域判据。有源音箱的 SPEAKER 缆误接已由上
    # 一条单独成码，这里不再重复报。
    for c in project.connections:
        if c.signal != Signal.SPEAKER:
            continue
        fd = by_dev.get(c.from_uid)
        td = by_dev.get(c.to_uid)
        if fd is None or td is None:
            continue
        if td.category == "SPEAKER" and td.active:
            continue  # 有源音箱误接已由上面 ACTIVE_ON_AMP_OUT 单独成码
        if td.category != "SPEAKER":
            # 扬声器缆误接线路设备（扬声器电平 → 线路/麦克风电平）
            issues.append(Issue("ERROR", "LEVEL_DOMAIN",
                f"{fd.name} 扬声器线缆误接至 {td.name}（{td.category}，非音箱）："
                f"扬声器电平接入线路设备将烧毁前端", td.uid))
        elif fd.category not in ("AMP", "SPEAKER_MGR"):
            # 线缆源自非功放/音响管理器（扬声器电平应由功放/音响管理器驱动）
            issues.append(Issue("ERROR", "LEVEL_DOMAIN",
                f"扬声器线缆源自 {fd.name}（{fd.category}，非功放/音响管理器）："
                f"扬声器电平应由功放或音响管理器驱动", fd.uid))

    # PHANTOM_MISSING（ERROR）：受 needs_phantom 标志门控，零误报。
    # 仅当某设备声明「需 P48」且其 XLR 输入上游未在 features 中标明提供幻象供电
    # 时才报。当前主库未给任何设备打 needs_phantom，故默认不触发；待主库标注
    # 电容麦后自动生效，无需改校验逻辑。
    for i in project.instances:
        if not (_truthy(i.params.get("needs_phantom")) or "needs_phantom" in i.features):
            continue
        for c in project.connections:
            if c.to_uid != i.uid or c.signal != Signal.XLR:
                continue
            prov = by_dev.get(c.from_uid)
            if prov is None or "phantom" not in prov.features:
                issues.append(Issue("ERROR", "PHANTOM_MISSING",
                    f"{i.name} 需幻象供电(P48)，但其上游 {prov.name if prov else c.from_uid}"
                    f" 未提供（features 无 phantom），电容麦将无法工作", i.uid))

    # ── Tier 3 音频/网络理解优化（能力门控 + 字段门控，零误报）─────────────────
    # 原则同 Tier 2：规则只在「能力标记(features) / 参数标记(params)」到位时才触发。
    # 当前主库未标任何 Tier 3 相关标记（aec/delay/vc/ptp/zone 等 features 与
    # line_type/tap_w/f_x/ptp_role/zone 等 params 均不存在）→ 全部休眠、零误报；
    # 待主库标注对应设备/字段后自动生效，无需改校验逻辑。
    # SPL_/STI_/COVERAGE_/RENDER_ 需 avcad/acoustics/ 房间模型（注册表明确不在
    # checks.py 范围），未在此实现，待建声学包后再落地。
    adj_up = defaultdict(set)
    for c in project.connections:
        if c.signal in _AUDIO_SIGNALS:
            adj_up[c.to_uid].add(c.from_uid)

    # ---------- AEC_ 会议回声消除（能力门控）----------
    # AEC_MISSING(WARN)：设备声明 needs_aec（要用 AEC）但系统内无任何带 aec 能力的
    #   DSP/处理器/会议主机 → 远端会议 + 本地话筒无回声消除，远端听到回声。
    # AEC_REF_UNCONNECTED(ERROR)：DSP 声明 aec 能力，但其参考输入口(XLR/CONF in)
    #   未接到远端终端输出 → AEC 取不到参考信号，失效。
    needs_aec = [i for i in project.instances
                 if _truthy(i.params.get("needs_aec")) or "needs_aec" in i.features]
    aec_dsp = any("aec" in d.features for d in project.instances)
    if needs_aec and not aec_dsp:
        nm = ", ".join(sorted(d.name for d in needs_aec))
        issues.append(Issue("WARN", "AEC_MISSING",
            f"{nm} 需回声消除(AEC) 但系统内无带 aec 能力的 DSP/处理器"
            f"（远端会议 + 本地话筒将产生回声）", needs_aec[0].uid))
    remote_terms = [d for d in project.instances
                    if "vc" in d.features or "sip" in d.features
                    or "remote" in d.features]
    if remote_terms:
        for i in project.instances:
            if "aec" not in i.features:
                continue
            ref_in = [p for p in i.ports if p.role == "in"
                      and p.signal in (Signal.XLR, Signal.CONF) and not p.air]
            if not ref_in:
                continue
            ref_used = {(c.to_uid, c.to_port) for c in project.connections
                        if c.to_uid == i.uid}
            if not any((i.uid, p.id) in ref_used for p in ref_in):
                issues.append(Issue("ERROR", "AEC_REF_UNCONNECTED",
                    f"{i.name} 声明 aec 能力，但参考输入口未接到远端终端输出"
                    f"（AEC 取不到参考信号将失效）", i.uid))

    # ---------- DELAY_ 补声/延时（能力门控）----------
    # DELAY_CAPABILITY_MISSING(ERROR)：扬声器声明 fill/delay_speaker（补声/延时音箱）
    #   但其上游音频链路上的处理器均无 delay 能力 → 补声与主扩无延时对齐，声像混乱。
    delay_spk = [i for i in project.instances
                 if (i.category == "SPEAKER"
                     and (_truthy(i.params.get("fill")) or "fill" in i.features))
                 or "delay_speaker" in i.features]
    for s in delay_spk:
        if not _upstream_has_feature(project, s.uid, "delay", adj_up):
            issues.append(Issue("ERROR", "DELAY_CAPABILITY_MISSING",
                f"{s.name} 为补声/延时音箱，但链路上的处理器均无 delay 能力"
                f"（无法做延时对齐，声像混乱）", s.uid))

    # ---------- PTP_ 时钟同步（参数门控）----------
    # PTP_GM_NONE(WARN)：系统含 Dante 且至少一台设备声明 ptp_role 参数，但无任何
    #   设备声明 ptp_role ∈ {gm, boundary}（无时钟主）→ 网络时钟靠默认选举，不确定。
    ptp_declared = [i for i in project.instances if i.params.get("ptp_role")]
    if ptp_declared:
        gms = [i for i in ptp_declared
               if str(i.params.get("ptp_role")).strip().lower()
               in ("gm", "boundary", "grandmaster")]
        if not gms:
            issues.append(Issue("WARN", "PTP_GM_NONE",
                f"系统含 Dante 但无设备显式承担 PTP 时钟主(ptp_role=gm/boundary)，"
                f"网络时钟靠默认选举，结果不确定", ""))

    # ---------- CV_ 定压（字段门控：仅读 line_type，缺字段即休眠）----------
    # 定压音箱(line_type ∈ 70V/100V/cv) 必须挂定压功放；混挂低阻或电压不一致即错。
    CV_TOKENS = {"70v", "100v", "cv"}
    for i in project.instances:
        if i.category != "SPEAKER":
            continue
        lt = str(i.params.get("line_type", "")).strip().lower()
        if lt not in CV_TOKENS:
            continue  # 未声明 line_type → 按低阻核算，不判 CV_（避免误报）
        # 找上游功放
        amp_uid = None
        for u in _audio_upstream(project, i.uid, adj_up):
            d = by_dev.get(u)
            if d is not None and d.category == "AMP":
                amp_uid = u
                break
        amp = by_dev.get(amp_uid) if amp_uid else None
        if amp is None:
            continue
        amp_lt = str(amp.params.get("line_type", "")).strip().lower()
        if amp_lt not in CV_TOKENS:
            issues.append(Issue("ERROR", "CV_MIXED",
                f"定压音箱 {i.name}({lt}) 挂在低阻功放 {amp.name} 上"
                f"（定压音箱须接定压功放）", i.uid))
        elif amp_lt != lt:
            issues.append(Issue("ERROR", "CV_VOLTAGE_MISMATCH",
                f"定压电压不一致：音箱 {i.name}({lt}) 接功放 {amp.name}({amp_lt})"
                f"（70V/100V 严禁混用）", i.uid))

    # ---------- XOVER_ 分频（字段门控：仅读 f_x）----------
    # XOVER_GAP(ERROR)：音箱声明分频点 f_x（列表）但点数不足以覆盖全频
    #   （<2 或非单调递增）→ 分频空洞。
    for i in project.instances:
        fx = i.params.get("f_x")
        if not isinstance(fx, (list, tuple)) or len(fx) < 2:
            if fx is not None:  # 显式声明了但无效
                issues.append(Issue("ERROR", "XOVER_GAP",
                    f"{i.name} 声明分频点 f_x={fx} 无效（需 ≥2 个单调递增频点）",
                    i.uid))
            continue

    # ---------- ZONE_ 分区（字段门控：仅读 zone）----------
    # ZONE_SINGLE_AMP(WARN)：某分区的音箱全部由 1 台功放驱动 → 分区单点。
    zone_amps = defaultdict(set)
    for i in project.instances:
        if i.category != "SPEAKER":
            continue
        z = i.params.get("zone")
        if not z:
            continue
        for u in _audio_upstream(project, i.uid, adj_up):
            d = by_dev.get(u)
            if d is not None and d.category == "AMP":
                zone_amps[z].add(u)
                break
    for z, amps in zone_amps.items():
        if len(amps) <= 1:
            issues.append(Issue("WARN", "ZONE_SINGLE_AMP",
                f"分区 {z} 的音箱全部由 1 台功放驱动（分区单点，功放故障整区哑）",
                ""))

    project.issues = issues
    return issues
