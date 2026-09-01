"""参考架构模板库 + 最优选择器（清单驱动工作流 步骤⑤）。

内置 10 套参考架构（对应 A-J 已确认场景）。依据 BOM 的类别覆盖、冗余需求评分选优，
并对需要冗余的架构给出 SPOF 提示（建议交换机数量）。出图前门禁 SPOF 由 validate/checks 负责，
这里在「选架构」阶段即给出可执行的冗余/Switch 建议。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ArchTemplate:
    id: str
    title: str
    desc: str
    required: List[str] = field(default_factory=list)   # 缺一项即严重不匹配
    optional: List[str] = field(default_factory=list)   # 命中加分
    redundancy_fit: List[str] = field(default_factory=list)  # 适配的冗余级别
    requires_redundancy: bool = False
    min_switches: int = 1                                # 真正冗余所需最少交换机数


TEMPLATES = [
    ArchTemplate("A_conference", "会议系统（基础音频+控制）",
                 "话筒/音源 → 处理器 → 扬声器，含控制网络。",
                 required=["SOURCE", "PROCESSOR", "SPEAKER"],
                 optional=["MIXER", "IO"], redundancy_fit=["NONE"]),
    ArchTemplate("B_wireless", "无线话筒系统（真分集）",
                 "发射 → 天线 → 天线分配 → 接收 → 处理器。",
                 required=["WIRELESS_MIC", "WIRELESS_RX", "PROCESSOR"],
                 optional=["ANTENNA", "ANT_DIST", "ANT_COMBINE"], redundancy_fit=["NONE"]),
    ArchTemplate("C_foh", "演出 FOH",
                 "音源 → 舞台接口箱(IO) → 处理器 → 功放 → 主扩。",
                 required=["SOURCE", "IO", "PROCESSOR", "SPEAKER"],
                 optional=["AMP", "SPEAKER_MGR"], redundancy_fit=["NONE"]),
    ArchTemplate("D_distributed", "分布式固定安装",
                 "多处理器分区 + 分布式扬声器。",
                 required=["PROCESSOR", "SPEAKER"],
                 optional=["SPEAKER_MGR", "AMP"], redundancy_fit=["NONE", "LINK_BACKUP"]),
    ArchTemplate("E_redundancy", "冗余双路径（主备）",
                 "处理器前置 → 主/备调音台，双交换机。",
                 required=["PROCESSOR", "MIXER"], optional=["SWITCH"],
                 redundancy_fit=["PROCESSOR_BACKUP", "LINK_BACKUP", "FULL_CHAIN"],
                 requires_redundancy=True, min_switches=2),
    ArchTemplate("F_theatre", "剧院主扩声",
                 "舞台话筒 → 舞台接口箱 → 调音台 → 处理器 → 音箱管理 → 功放 → 主扩/超低/返听。",
                 required=["IO", "PROCESSOR", "SPEAKER"],
                 optional=["SOURCE", "MIXER", "SPEAKER_MGR", "AMP"],
                 redundancy_fit=["NONE", "LINK_BACKUP"]),
    ArchTemplate("G_studio", "录音/转播",
                 "话筒 → 接口箱 → 控制台 → 监听管理 → 监听功放 → 主监听/近场。",
                 required=["IO", "MIXER", "SPEAKER"],
                 optional=["SPEAKER_MGR", "AMP"], redundancy_fit=["NONE"]),
    ArchTemplate("H_pa", "公共广播",
                 "寻呼话筒 → 分区调音台 → 广播处理器 → 多区音箱管理 → 分区功放 → 吸顶/壁挂。",
                 required=["PROCESSOR", "SPEAKER_MGR", "SPEAKER"],
                 optional=["MIXER", "AMP"], redundancy_fit=["NONE", "LINK_BACKUP"]),
    ArchTemplate("I_touring", "流动演出（主备）",
                 "无线 16 路 → 天线分配 → 接收 → 主/备调音台 → 音箱管理 → 功放 → 主/返听。",
                 required=["WIRELESS_RX", "MIXER", "PROCESSOR"],
                 optional=["WIRELESS_MIC", "ANT_DIST", "SPEAKER_MGR", "AMP"],
                 redundancy_fit=["FULL_CHAIN", "LINK_BACKUP"],
                 requires_redundancy=True, min_switches=2),
    ArchTemplate("J_multifunc", "多功能厅",
                 "有线话筒 + 无线接收 → 调音台 → 处理器 → 音箱管理 → 功放 → 主/辅助/返听。",
                 required=["MIXER", "PROCESSOR", "SPEAKER"],
                 optional=["SOURCE", "WIRELESS_RX", "SPEAKER_MGR", "AMP"],
                 redundancy_fit=["NONE", "LINK_BACKUP"]),
]


def profile_of(entries: List[dict]) -> set:
    return {str(e.get("category", "")).upper() for e in entries if e.get("category")}


def _score(t: ArchTemplate, cats: set, redundancy: Optional[str]) -> Tuple[float, List[str]]:
    """评分策略：必需类别全满足为门槛（缺一项重罚）；满足后按
    - 必需类别数×10（越具体越高）
    - 必需∪可选 对 BOM 的覆盖率 +1/类
    - 冗余适配 +4 / 不适配 -1
    使「会议系统」等主分类在混合 BOM 中仍优先于泛化「多功能厅」。"""
    score = 0.0
    notes = []
    req = set(t.required)
    opt = set(t.optional)
    miss = req - cats
    if miss:
        score = -10 * len(miss)
        notes += [f"缺必需类别 {c}" for c in miss]
    else:
        score = 10 * len(req)
        cover = (req | opt) & cats
        score += len(cover)
    if t.redundancy_fit:
        if redundancy in t.redundancy_fit:
            score += 4
        elif redundancy and redundancy != "NONE":
            score -= 1
    if t.requires_redundancy:
        if not redundancy or redundancy == "NONE":
            score -= 3
            notes.append("本架构建议配置主备冗余")
        else:
            notes.append(f"冗余建议 ≥{t.min_switches} 台 Dante 交换机（避免 SPOF）")
    elif redundancy and redundancy != "NONE" and t.min_switches >= 2:
        notes.append(f"冗余建议 ≥{t.min_switches} 台 Dante 交换机（避免 SPOF）")
    return score, notes


def select(entries: List[dict], redundancy: Optional[str] = None
           ) -> List[Tuple[ArchTemplate, float, List[str]]]:
    cats = profile_of(entries)
    scored = [_score(t, cats, redundancy) + (t,) for t in TEMPLATES]
    # scored: (score, notes, t)
    out = [(t, s, n) for (s, n, t) in scored]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def recommended(entries: List[dict], redundancy: Optional[str] = None
                ) -> Tuple[ArchTemplate, float, List[str]]:
    ranked = select(entries, redundancy)
    return ranked[0]
