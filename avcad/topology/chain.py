"""拓扑链路：数据驱动的阶段顺序（处理器前置/后置）、无线真分集子系统、主备分组。

处理器相对调音台的位置（阳哥规则 2026-08-29）：
- 默认「前置」：处理器放在调音台之前（SOURCE → 处理器 → 调音台 → 功放 → 扬声器）。
- 例外「后置」：处理器参数 position=后置（或 proc_func=system）时放在调音台之后。
- 强制前置：① 功放带 DSP 处理功能；② 处理器参与主备冗余。
"""
from __future__ import annotations
from avcad.model.schema import DeviceInstance, Redundancy

# 处理器两种放置阶段的内部键（用独立 stage 以便同系统混排前置/后置处理器）
PROC_PRE = "PROC_PRE"
PROC_POST = "PROC_POST"

# 阶段中文标签（汇总/UI 用；内部布局仍以 stage 键为准）
STAGE_LABELS = {
    PROC_PRE: "处理器(前置)",
    PROC_POST: "处理器(后置)",
    "SOURCE": "音源",
    "WIRELESS_MIC": "无线话筒",
    "WIRELESS_RX": "无线接收机",
    "ANTENNA": "天线",
    "ANT_DIST": "天线信号分配",
    "MIC_HOST": "话筒主机",
    "MIXER": "调音台",
    "IO": "接口箱/扩展",
    "SPEAKER_MGR": "扬声器管理",
    "AMP": "功放",
    "SPEAKER": "扬声器",
    "SWITCH": "Dante交换机",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def _amp_has_dsp(inst: DeviceInstance) -> bool:
    """判定功放是否带 DSP 处理功能（多重信号，覆盖主库'带DSP'命名与显式标记）。"""
    feats = {str(f).lower() for f in (inst.features or set())}
    if "dsp" in feats:
        return True
    params = inst.params or {}
    if params.get("dsp"):
        return True
    if str(params.get("proc_func") or "").strip():
        return True
    if "DSP" in (inst.model or "").upper():
        return True
    return False


def _project_has_dsp_amp(instances: list) -> bool:
    return any(_amp_has_dsp(i) for i in instances if i.category == "AMP")


def _proc_position(inst: DeviceInstance, amp_has_dsp: bool) -> str:
    """返回 'pre'（前置/调音台前）或 'post'（后置/调音台后）。默认前置。"""
    # 强制前置：功放带 DSP，或处理器参与主备冗余
    if amp_has_dsp:
        return "pre"
    if inst.redundancy not in (Redundancy.NONE,):
        return "pre"
    # 显式 position 参数优先（清单/图例可写 position=前置|后置）
    pos = str((inst.params or {}).get("position") or "").strip()
    if pos in ("前置", "pre", "before"):
        return "pre"
    if pos in ("后置", "post", "after"):
        return "post"
    # 退化：proc_func 语义 automix=前置, system=后置
    pf = str((inst.params or {}).get("proc_func") or "").lower()
    if pf == "automix":
        return "pre"
    if pf == "system":
        return "post"
    # 默认前置
    return "pre"


def build_chain(instances: list) -> list:
    """返回有序阶段（stage 列表），不含 SWITCH（交换机为侧层）。"""
    cats = {i.category for i in instances}
    amp_has_dsp = _project_has_dsp_amp(instances)
    proc_pos = {_proc_position(i, amp_has_dsp) for i in instances
                if i.category == "PROCESSOR"}

    wireless = bool(cats & {"WIRELESS_MIC", "WIRELESS_RX", "ANTENNA"})
    wchain = []
    if wireless:
        # 无线接收机（WIRELESS_RX）属于音源输出端，与 SOURCE 同列
        wchain = ["WIRELESS_MIC", "ANTENNA", "ANT_DIST"]
        wchain = [c for c in wchain if c in cats or c == "ANT_DIST"]  # ANT_DIST 缺失时自动补

    chain = list(wchain)
    if "SOURCE" in cats or "WIRELESS_RX" in cats:
        chain.append("SOURCE")   # 有线话筒/无线接收机/CD 等音源级，接入首个核心级

    # 核心级：前置处理器 → 调音台 → 后置处理器
    if "pre" in proc_pos:
        chain.append(PROC_PRE)
    if "MIXER" in cats:
        chain.append("MIXER")
    if "post" in proc_pos:
        chain.append(PROC_POST)

    if "IO" in cats:
        chain.append("IO")       # I/O 接口箱/扩展卡：紧接核心设备，作为扩展接口
    if "SPEAKER_MGR" in cats:
        chain.append("SPEAKER_MGR")
    # 功放：只要有独立功放实例就保留阶段（即使扬声器全有源，也按用户清单画出）
    if "AMP" in cats:
        chain.append("AMP")
    if "SPEAKER" in cats:
        chain.append("SPEAKER")
    # 去重保序
    seen = set()
    return [c for c in chain if not (c in seen or seen.add(c))]


def assign_stages(instances: list, chain: list):
    amp_has_dsp = _project_has_dsp_amp(instances)
    for i in instances:
        if i.category == "WIRELESS_RX":
            # 无线接收机属于音源输出端，与 SOURCE 同列布局
            i.stage = "SOURCE"
        elif i.category == "MIC_HOST":
            # 话筒主机（会议主机）：话筒汇总后送核心级，与 SOURCE 同列
            i.stage = "SOURCE"
        elif i.category == "PROCESSOR":
            i.stage = PROC_PRE if _proc_position(i, amp_has_dsp) == "pre" else PROC_POST
        elif i.category in chain:
            i.stage = i.category
        elif i.category == "SWITCH":
            i.stage = "SWITCH"
        else:
            i.stage = i.category


def pair_redundancy(instances: list) -> list:
    """按类别聚合冗余设备并建立主备分组，返回未能成组的告警列表。

    ★ 此前「清单里写了冗余列但同类别不足 2 台」会**静默失效**——用户以为
    设了冗余，图上却没有任何变化。现在一律记告警，由 build_project 收进
    project.meta，报告里能看见。

    注意：`REDUNDANCY_SCOPE[级别].categories` 只决定 `_apply_redundancy`
    **复制哪些类别**，不限制这里能配对的类别——清单里给 2 台调音台标
    LINK_BACKUP（意为「这一对走双链路冗余」）是合法用法，不能被判越界。
    """
    by_cat = {}
    for i in instances:
        if i.redundancy in (Redundancy.NONE,) or not i.redundancy:
            continue
        by_cat.setdefault(i.category, []).append(i)

    warns = []
    for cat, grp in by_cat.items():
        if len(grp) >= 2:
            grp[0].redundant_group = cat + "_grp"
            grp[1].redundant_group = cat + "_grp"
            grp[0].pair = grp[1].uid
            grp[1].pair = grp[0].uid
            grp[0].is_backup = False
            grp[1].is_backup = True
            if len(grp) > 2:
                extra = "、".join(g.name for g in grp[2:])
                warns.append(f"{cat} 类有 {len(grp)} 台标了冗余，只取前 2 台配成主备，"
                             f"其余未参与：{extra}")
        else:
            d = grp[0]
            lvl = d.redundancy.value if hasattr(d.redundancy, "value") else str(d.redundancy)
            warns.append(
                f"{d.name} 标了冗余「{lvl}」，但同类别只有 1 台，无法组成主备——"
                f"需要 2 台同型号设备，或在第④步选带主备的候选架构")
    return warns
