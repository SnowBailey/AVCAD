"""GB 55024-2022《建筑电气与智能化通用规范》全文强制项 → AVCAD 校验映射。

★ 定位：可选量化字段，零 schema 破坏。
  本模块不往 DeviceInstance 新增大字段，只把「全文强制强条」作为**数据 registry** 持有，
  并依据项目数据发出 INFO/WARN/ERROR 三级提醒：

  - 基础提醒（INFO/WARN）只靠**现有**数据触发（POWER 端口、`electrical` 字典、
    features、`meta` 标记），不要求任何额外字段。
  - 量化硬指标（接地跨接线径、应急广播声压级）可在 DeviceInstance 的 `electrical`
    字典里**可选**填入下列键来升级为 ERROR 级精确判定；不填则退回 WARN 提醒，不误杀：
      - `ground_wire_mm2`：接地/等电位联结导体截面积（mm²），强条要求 ≥4 mm²。
      - `emg_spl_db`      ：应急广播设计声压级（dB，听众处）。
      - `bg_spl_db`       ：背景噪声声压级（dB），用于「背景+15」 Margin 判定（可选）。
    系统级也可在 `project.meta` 填 `emergency_broadcast_spl_db` / `background_spl_db`，
    优先级高于逐设备 `electrical` 键。

★ 强条双锚原则（doc 06 §附录 C）：消防引 GB 55036-2022、电气引 GB 55024-2022、
环境引 GB 55016-2021，老标准强条已被其吞并，引用老号即失效。

★ 数据先于规则：所有条款条目放在 STANDARDS_REGISTRY，新增/修订条款只改这个表，
不散落到代码逻辑里。

条款来源：av-kb-research/findings/06_行业标准与规范.md（E2-12 / E2-13 / 附录 C）。
"""
from __future__ import annotations

from avcad.model.schema import Issue, Signal

# ---------------------------------------------------------------------------
# 数据 registry：GB 55024-2022 全文强制体系相关强条（仅收录可从现有数据判定的）
# ---------------------------------------------------------------------------
STANDARDS_REGISTRY = [
    {
        "rule": "DESIGN_BASIS",
        "std": "GB 55024-2022",
        "clause": "全文（通用规范，2022-10-01 实施）",
        "level": "mandatory",
        "title": "建筑电气与智能化通用规范（全文强制）",
        "note": "废止 GB 50303-2015 等 11 项标准共 17 条强条；电气/接地设计依据应列本规范。",
    },
    {
        "rule": "GROUNDING",
        "std": "GB 55024-2022 / GB 50303-2015",
        "clause": "GB 50303-2015 第 5.1.1 条（仍有效，归属 55024 体系）",
        "level": "mandatory",
        "title": "柜/台/箱金属框架与门跨接接地",
        "note": "柜、台、箱的金属框架及基础型钢、可开启门与框架间，应用 ≥4 mm² 黄绿相间绝缘软铜导线跨接。",
    },
    {
        "rule": "EMG_BROADCAST",
        "std": "GB 55036-2022",
        "clause": "第 12.0.5 条 / 第 12.0.9 条（全文强制）",
        "level": "mandatory",
        "title": "消防应急广播声压级与强制切入",
        "note": "声压级 ≥ max(60, 背景+15) dB；合用系统须能强制切入。老号 GB 50116-2013 第 4.8.x 强条已被 55036 废止。",
    },
]

# 接地跨接导体截面积强条下限（mm²，GB 50303-2015 第 5.1.1 条）
GROUND_WIRE_MIN_MM2 = 4.0
# 应急广播声压级硬下限（dB，GB 55036-2022 第 12.0.5 条）
EMG_SPL_FLOOR_DB = 60.0
EMG_SPL_MARGIN_DB = 15.0

_EMERGENCY_FEATURE_HINTS = ("emg", "emergency", "应急", "消防", "fire")


def _devices_needing_grounding(project) -> list:
    """需要接地跨接的设备：有 POWER 端口或填了 electrical 字典（不靠类别硬编码）。"""
    out = []
    for i in project.instances:
        has_power = any(p.signal == Signal.POWER for p in i.ports)
        if has_power or i.electrical:
            out.append(i)
    return out


def _has_emergency_broadcast(project) -> bool:
    """项目是否含消防/应急广播（看 features 关键词 + meta 标记 + 相关类别）。"""
    if project.meta.get("emergency_broadcast"):
        return True
    for i in project.instances:
        for f in i.features:
            fl = str(f).lower()
            if any(h in fl for h in _EMERGENCY_FEATURE_HINTS):
                return True
        if i.category in ("PUBLIC_ADDRESS", "EMG_BROADCAST", "EVAC"):
            return True
    return False


def _emg_spl_db(project):
    """应急广播设计声压级（dB）：meta 优先，否则取任一带 emg_spl_db 的设备。缺则 None。"""
    v = project.meta.get("emergency_broadcast_spl_db")
    if v is not None:
        return float(v)
    for i in project.instances:
        e = i.electrical or {}
        if e.get("emg_spl_db") is not None:
            return float(e["emg_spl_db"])
    return None


def _bg_spl_db(project):
    """背景噪声声压级（dB）：meta 优先，否则取任一带 bg_spl_db 的设备。缺则 None。"""
    v = project.meta.get("background_spl_db")
    if v is not None:
        return float(v)
    for i in project.instances:
        e = i.electrical or {}
        if e.get("bg_spl_db") is not None:
            return float(e["bg_spl_db"])
    return None


def check_standards(project) -> list:
    """返回 GB 55024-2022 全文强制相关的设计依据提醒（INFO/WARN/ERROR）。

    纯数据驱动，不改任何字段；量化硬指标仅在 `electrical`/`meta` 填了对应键时升级为 ERROR，
    否则退回 WARN 提醒，保证不误杀正确图纸。
    """
    issues: list = []

    devices = _devices_needing_grounding(project)
    if devices:
        # INFO：设计依据应列 GB 55024-2022 全文强制
        issues.append(Issue(
            "INFO", "GB55024_DESIGN_BASIS",
            "项目含电气设备：设计依据应列 GB 55024-2022《建筑电气与智能化通用规范》"
            "（全文强制，2022-10-01 实施，已废止 GB 50303-2015 等 11 项 17 条强条）",
            ""))

        # 量化硬指标：接地跨接导体截面积 ≥4 mm²（填了才精确判定）
        offenders = [
            i.uid for i in devices
            if (i.electrical or {}).get("ground_wire_mm2") is not None
            and float((i.electrical or {}).get("ground_wire_mm2")) < GROUND_WIRE_MIN_MM2
        ]
        if offenders:
            issues.append(Issue(
                "ERROR", "GB55024_GROUNDING_WIRE",
                "接地跨接导体截面积不足（须 ≥%.0f mm² 黄绿相间绝缘软铜导线，"
                "GB 50303-2015 第 5.1.1 条，归属 GB 55024-2022 体系）：%s。"
                % (GROUND_WIRE_MIN_MM2, "、".join(offenders)),
                ""))
        elif all((i.electrical or {}).get("ground_wire_mm2") is not None for i in devices):
            # 全部已填且合规 → 不报接地问题（仅保留上面的设计依据 INFO）
            pass
        else:
            # 部分/全部未填线径 → 无法量化，发 WARN 提醒人工核对
            issues.append(Issue(
                "WARN", "GB55024_GROUNDING",
                "含电气设备：柜/台/箱金属框架与可开启门间须用 ≥%.0f mm² 黄绿相间绝缘软铜导线跨接"
                "（GB 50303-2015 第 5.1.1 条，仍有效，归属 GB 55024-2022 体系）。"
                "当前数据未记录线径，请人工核对接地跨接"
                % GROUND_WIRE_MIN_MM2,
                ""))

    if _has_emergency_broadcast(project):
        spl = _emg_spl_db(project)
        bg = _bg_spl_db(project)
        if spl is not None:
            required = EMG_SPL_FLOOR_DB
            if bg is not None:
                required = max(EMG_SPL_FLOOR_DB, bg + EMG_SPL_MARGIN_DB)
            if spl < required:
                issues.append(Issue(
                    "ERROR", "GB55024_EMG_SPL",
                    "消防应急广播声压级不足：设计 %.0f dB < 要求 ≥%.0f dB"
                    "（GB 55036-2022 第 12.0.5 条：≥ max(60, 背景+15) dB）。"
                    % (spl, required),
                    ""))
            # 合规 → 不报
        else:
            # 未填声压级 → 无法量化，发 WARN 提醒（强条双锚 + 改引 55036）
            issues.append(Issue(
                "WARN", "GB55024_EMG_BROADCAST",
                "含消防/应急广播：声压级须 ≥ max(60, 背景+15) dB 且合用系统须强制切入"
                "（GB 55036-2022 第 12.0.5/12.0.9 条，全文强制）。"
                "引用老号 GB 50116-2013 第 4.8.x 强条已失效，须改引 55036",
                ""))

    return issues
