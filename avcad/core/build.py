"""工程编排：清单 -> 实例 -> 链路 -> 布局 -> 连线 -> 校验 -> 工程对象。含冗余候选拓扑。"""
from __future__ import annotations
from avcad.model.schema import (Project, DeviceInstance, Redundancy,
                                redundancy_scope, redundancy_levels)
from avcad.model.specs import build_instances, load_specs, expand_instance
from avcad.parse.product_resolver import enrich
from avcad.topology.chain import build_chain, assign_stages, pair_redundancy
from avcad.layout.engine import place
from avcad.wires.router import connect
from avcad.validate.checks import validate


def _ensure_wireless_dist(entries):
    """清单里没配天线分配器时自动补一台——但只在**确实需要**时补。

    两个前提缺一不可，否则会凭空造出一台孤立设备：
      1. 有接收机（WIRELESS_MIC / WIRELESS_RX）等着分天线信号；
      2. 有**外接天线**——会讨天线盒（``params.conf_box``，如 IPS CF6300WB）
         自带 UHF 接收，经六芯主缆回主机 BOX 口，不进分配器链路。
    """
    cats = {str(e.get("category", "")).upper() for e in entries}
    if "ANT_DIST" in cats:
        return entries
    receivers = bool(cats & {"WIRELESS_MIC", "WIRELESS_RX"})
    ext_antenna = any(
        str(e.get("category", "")).upper() == "ANTENNA"
        and not (e.get("params") or {}).get("conf_box")
        for e in entries)
    if receivers and ext_antenna:
        entries = list(entries) + [{
            "category": "ANT_DIST", "name": "天线信号分配器", "quantity": 1,
            "params": {"inputs": 2, "outputs": 4},
        }]
    return entries


def _make_switches(instances, redundant: bool):
    specs = load_specs()
    spec = specs["SWITCH"]
    n_dante = sum(1 for i in instances for p in i.ports if p.signal.name == "DANTE")
    ports = max(8, min(24, n_dante + 2))
    sw = []
    for k in range(2 if redundant else 1):
        e = {"category": "SWITCH", "name": "Dante 交换机" + ("(备)" if k else ""),
             "params": {"ports": ports}}
        inst = expand_instance(spec, e, k)
        inst.redundancy = Redundancy.LINK_BACKUP if redundant else Redundancy.NONE
        if k == 1:
            inst.is_backup = True
        sw.append(inst)
    return sw


def _clone_as_backup_switch(primary):
    """按主交换机克隆一台备交换机（同品牌/型号/端口数）。

    用于「清单只配了 1 台交换机，但冗余级别要求双交换机」的场景。此前
    `switches = real_switches` 直接返回，链路冗余会静默退化成单链路——
    只在报告里留一条 SPOF 警告，图上仍然只有一台交换机，主备设备还是
    挤在同一台交换机上，冗余形同虚设。
    """
    specs = load_specs()
    spec = specs["SWITCH"]
    e = {"category": "SWITCH",
         "name": (primary.name or "Dante 交换机") + "(备)",
         "brand": primary.brand, "model": primary.model,
         "params": dict(primary.params or {}),
         # uid 必须显式给定：expand_instance 默认按 `switch_{idx+1}` 生成，
         # 与清单里的真交换机编号同段，端口/连线索引会撞车。
         "uid": f"{primary.uid}_bak"}
    inst = expand_instance(spec, e, 0)
    inst.redundancy = Redundancy.LINK_BACKUP
    inst.is_backup = True
    inst.pair = primary.uid
    primary.pair = inst.uid
    return inst


def build_project(entries: list, name: str = "AV System",
                  legend_store=None, redundancy: str = None) -> Project:
    """legend_store: 可选 LegendStore；若提供，实例建成后立即按缓存图例回填端口
    （用户确认过的型号自动套用，未确认则保留规格默认）。

    redundancy: 工程级冗余级别（DEVICE_BACKUP / PROCESSOR_BACKUP / LINK_BACKUP /
    FULL_CHAIN）。用于两件条目层面办不到的事：① 清单里没有交换机时仍要按级别
    生成双交换机（LINK_BACKUP 的冗余载体就是交换机本身，条目里可能根本没有它）；
    ② 记入 meta 供报告与告警使用。条目自带的 redundancy 与之叠加生效。
    """
    entries = enrich(list(entries))
    entries = _ensure_wireless_dist(entries)
    instances = build_instances(entries)
    if legend_store is not None:
        for inst in instances:
            legend_store.apply(inst)
    chain = build_chain(instances)
    redundancy_warnings = pair_redundancy(instances)
    assign_stages(instances, chain)

    has_dante = any(p.signal.name == "DANTE" for i in instances for p in i.ports)
    redundant_dante = any(i.is_backup and any(p.signal.name == "DANTE" for p in i.ports)
                          for i in instances)
    # 链路级冗余（LINK_BACKUP / FULL_CHAIN）要求双交换机，与「备机是否有 Dante 口」
    # 无关——这是冗余级别本身的要求，不是备机端口的副作用。
    # 工程级级别也要算进来：LINK_BACKUP 的冗余载体就是交换机，而清单里可能
    # 根本没列交换机，此时只能靠工程级级别驱动。
    level_dual = redundancy_scope(redundancy)["dual_switch"] or any(
        redundancy_scope(i.redundancy)["dual_switch"] for i in instances)
    # 清单里明确配了交换机（如 VINGLOOP AIM-24MG6XF-UPoE、L-Acoustics LS10）时
    # 直接用清单的，不要再凭空造一台虚拟交换机——否则会出现
    # 「虚拟交换机连满、清单里的真交换机成了孤立节点」的怪图。
    real_switches = [i for i in instances if i.category == "SWITCH"]
    if real_switches:
        switches = list(real_switches)
        # ★ 清单只配 1 台交换机、但冗余级别要求双交换机时，按首台克隆一台备机。
        #   此前这里直接返回清单交换机，LINK_BACKUP / FULL_CHAIN 会静默退化成
        #   单链路——报告里只有一条 SPOF 警告，图上主备设备仍挤在同一台交换机上。
        if len(switches) == 1 and has_dante and (level_dual or redundant_dante):
            switches.append(_clone_as_backup_switch(switches[0]))
    else:
        sw_redundant = redundant_dante or level_dual
        switches = _make_switches(instances, sw_redundant) if has_dante else []

    proj = Project(name=name, instances=instances, chain=chain, switches=switches)
    size = place(instances, chain, switches)
    proj.meta.update(size)
    # 工程级冗余级别要在 connect() 之前落 meta：连线阶段（_failover）需要知道
    # 本方案是否属于「链路冗余」（该档不画设备间 failover 线）。
    if redundancy:
        proj.meta["redundancy"] = (redundancy.value if isinstance(redundancy, Redundancy)
                                   else str(redundancy))
    if redundancy_warnings:
        proj.meta["redundancy_warnings"] = redundancy_warnings
    connect(proj)
    validate(proj)
    return proj


def _clone_entries(entries):
    out = []
    for e in entries:
        e2 = dict(e)
        e2["params"] = dict(e.get("params", {}))
        e2["features"] = list(e.get("features", []) or [])
        out.append(e2)
    return out


def _apply_redundancy(es, level):
    """按冗余级别把该级别管辖的设备类别各复制成主/备两台并互指 pair。

    ★ 复制哪些类别由 `schema.REDUNDANCY_SCOPE` 决定，这里不再接受外部 mapping——
    此前 app.py / run.py / generate_candidates 三处各写一份 `{"MIXER": lvl}`，
    导致「T2 标调音台主备却用 PROCESSOR_BACKUP 这个枚举值」的命名错位。
    """
    cats = redundancy_scope(level)["categories"]
    if not cats:
        return list(es)
    out = []
    for e in es:
        cat = str(e.get("category", "")).upper()
        if cat in cats:
            lvl = level.value if isinstance(level, Redundancy) else str(level)
            p = dict(e)
            p["params"] = dict(e.get("params", {}))
            p["features"] = list(e.get("features", []) or [])
            p["quantity"] = 1
            p["redundancy"] = lvl
            p["uid"] = f"{cat.lower()}_MAIN"
            p["pair"] = f"{cat.lower()}_BAK"
            b = dict(p)
            b["name"] = (e.get("name") or cat) + "(备)"
            b["uid"] = f"{cat.lower()}_BAK"
            b["pair"] = f"{cat.lower()}_MAIN"
            out += [p, b]
        else:
            out.append(e)
    return out


def generate_candidates(entries: list, name: str = "AV System"):
    """确定性生成候选拓扑（按冗余维度），供用户选择。

    ★ 每档冗余一个候选，标签与枚举语义一一对应（此前 T2 标「调音台主备」
    却用 PROCESSOR_BACKUP，而该枚举按语义应复制处理器，属命名错位）。
    """
    base = enrich(list(entries))
    base = _ensure_wireless_dist(base)

    plans = [
        ("T1 基础（无主备，单链路）", "NONE"),
        ("T2 调音台主备（设备级热备）", "DEVICE_BACKUP"),
        ("T3 处理器主备（核心热备）", "PROCESSOR_BACKUP"),
        ("T4 链路主备（Dante 双交换机，不增末端设备）", "LINK_BACKUP"),
        ("T5 全链路主备（调音台+处理器+双交换机）", "FULL_CHAIN"),
    ]

    projects = []
    for label, lvl in plans:
        es = _apply_redundancy(_clone_entries(base), lvl) if lvl != "NONE" \
            else _clone_entries(base)
        try:
            p = build_project(es, name=f"{name} · {label}", redundancy=lvl)
            p.meta["candidate_label"] = label
            projects.append((label, p))
        except Exception:
            projects.append((label, None))
    return projects
