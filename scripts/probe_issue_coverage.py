"""校验覆盖度探针：统计各类 Issue 在真实清单里实际被触发的次数。

背景（2026-08-31）：`scripts/probe_link_coverage.py` 找出 4 个「从未被真实数据
走到」的连线函数，里面藏着 1 处崩溃 + 3 处画错。同样的逻辑适用于校验层——
**一条 Issue 如果从来没被触发过，有两种可能**：

  1. 系统一直很干净（好事，说明规则被遵守）；
  2. 该报错的没报（坏事，校验逻辑有 bug 或条件写错）。

区分不了就只能人工核对：对零命中的 Issue 码，构造一个「必然违规」的最小
场景喂进去，看它报不报。报不出来 = 漏报，是真缺陷。

用法：python3 scripts/probe_issue_coverage.py
退出码：0 = 全部 Issue 码至少触发过一次；1 = 存在零命中（仅提示，非失败）
"""
from __future__ import annotations
import os
import re
import sys
import collections

sys.path.insert(0, ".")

from avcad.workflow.importers import build_entries  # noqa: E402
from avcad.core.build import build_project  # noqa: E402
from avcad.validate.checks import validate  # noqa: E402

YOUTENG = "/Users/mac/Desktop/202601/友腾-EAW音频扩声20260807.xlsx"
TAIYANG = "/Users/mac/Desktop/202601/文博-太阳纸业20260806.xlsx"
HEZE = "/Users/mac/Desktop/202601/华演出-菏泽曹州古城广场演出系统20260813.xlsx"

JOBS = [
    ("A-菏泽曹州古城", HEZE, None),
    ("B-L-ACOUSTICS", YOUTENG, "L-ACOUSTICS"),
    ("B-EAW1", YOUTENG, "EAW1"),
    ("B-EAW2", YOUTENG, "EAW2"),
    ("B-EAW3_KF210", YOUTENG, "EAW3 KF210"),
    ("B-EAW4", YOUTENG, "EAW4"),
    ("C-1F会议室", TAIYANG, "1F会议室"),
    ("C-2F会议室", TAIYANG, "2F会议室"),
    ("C-3F会议室", TAIYANG, "3F会议室"),
    ("C-4F会议室", TAIYANG, "4F会议室"),
]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS_PY = os.path.join(_ROOT, "avcad", "validate", "checks.py")
VALIDATE_DIR = os.path.join(_ROOT, "avcad", "validate")


def source_codes():
    """从 avcad/validate/ 下全部模块源码里正则提取全部 Issue 码。

    ★ 此前只扫 checks.py，硬编码名单的坑（PORT_SIDE/PORT_ROLE 漏同步）修掉后又
      冒出新的盲区：GB 55024 强条提醒搬到了独立的 standards_gb55024.py，只扫
      checks.py 就会把它的码报成「源码里没有的 Issue 码」。改成扫整个 validate/
      目录后，新增校验模块自动进入统计，漏登记会立刻被报为存疑。
    """
    found = []
    for path in sorted(os.listdir(VALIDATE_DIR)):
        if not path.endswith(".py"):
            continue
        src = open(os.path.join(VALIDATE_DIR, path), encoding="utf-8").read()
        found += re.findall(
            r'Issue\(\s*["\'][A-Z]+["\']\s*,\s*["\']([A-Z0-9_]+)["\']', src)
    assert found, (
        f"从 {VALIDATE_DIR} 里没提取到任何 Issue 码——Issue(...) 的写法变了？"
        f"请更新本函数的正则，别回退成硬编码名单。"
    )
    return sorted(set(found))

# 已人工构造「必然违规」场景验证过：规则本身能正常触发，真实清单零命中
# 只是因为这些清单确实合规。**不要**再当成疑似漏报重复排查。
VERIFIED_FIRE_OK = {
    "UNKNOWN_TYPE": "此前真实清单触发过 1 次，是 MIC_HOST 被误报（类别漏在 checks.py "
                    "的硬编码白名单里）；白名单改成从 load_specs() 动态取后归零，"
                    "属修复效果不是漏报",
    "DIVERSITY": "1 路天线输入的接收机 → ERROR 正常报",
    "SPOF_NET_SHARED_SWITCH": "主备 Dante 设备共用交换机（S_p∩S_b≠∅）→ ERROR 正常报（C1 集合判据修复）",
    "SPOF_NET_NO_DUAL_SWITCH": "有主备却仅 1 台交换机 → WARN 正常报（安全网）",
    "SPOF_DSP_SINGLE": "信号链路单点处理器（割点判定）→ WARN 正常报；叶子/星形拓扑/已配 PROCESSOR_BACKUP 不误报",
    "DANTE_NO_SWITCH_HOP": "Dante 设备↔设备直连（两端均非交换机）→ WARN 正常报；正常 device↔switch 不误报",
    "DANTE_NO_NETWORK": "Dante 连通分量无交换机（device↔device 组网）→ WARN 正常报；含交换机的分量不误报",
    "DANTE_NO_CONNECTION": "S8：有 Dante 端口却零 Dante 连线（漏接 Dante 网络盲区）→ WARN 正常报；"
                          "已通过 test_checks_s8_s9_s10.py 构造「有 Dante 端口设备未接任何 DANTE 连接」场景验证触发",
    "PAIR_MISSING": "pair 指向不存在的 uid → ERROR 正常报",
    "PAIR_TYPE": "主备跨类别配对 → ERROR 正常报",
    "REDUNDANCY": "同类只有 1 台却标冗余 → WARN 正常报",
    "REDUNDANT_NO_FAILOVER": "S9：声明主备冗余却没画 failover 备份线（静默假冗余）→ WARN 正常报；"
                            "已通过 test_checks_s8_s9_s10.py 构造「设了 pair+需 failover_link 冗余却无 backup 连线」场景验证触发",
    "LINK_BACKUP_NO_DANTE": "标链路冗余但无 Dante 设备 → WARN 正常报",
    "GB55024_DESIGN_BASIS": "③：项目含电气设备即触发 INFO 设计依据提醒；已通过 test_standards_gb55024.py 验证；"
                            "真实清单含电气设备的 10 份各触发 1 次",
    "GB55024_GROUNDING": "③：含电气设备即触发 WARN 接地跨接提醒（≥4mm² 黄绿软铜线）；"
                         "已通过 test_standards_gb55024.py 验证；真实清单 10 份各触发 1 次",
    "GB55024_EMG_BROADCAST": "③：含消防/应急广播特征（features 含 EMG 等）即触发 WARN 强条双锚；"
                             "已通过 test_standards_gb55024.py 构造 EMG 场景验证触发；"
                             "真实清单无应急广播特征，零命中是合规预期",
    "GB55024_GROUNDING_WIRE": "③ERROR升级：设备 electrical['ground_wire_mm2']<4mm² 即触发 ERROR 接地跨接不足；"
                              "已通过 test_standards_gb55024.py 构造线径 2.5mm² 场景验证触发；"
                              "真实清单未填线径键，零命中是待补数据预期（退回 WARN）",
    "GB55024_EMG_SPL": "③ERROR升级：应急广播声压级(电气emg_spl_db或meta emergency_broadcast_spl_db)"
                       "<max(60,背景+15)dB 即触发 ERROR；已通过 test_standards_gb55024.py "
                       "构造 55dB/62dB(背景55) 场景验证触发；真实清单未填声压级键，零命中是待补数据预期",
    "ACTIVE_ON_AMP_OUT": "Tier2：有源音箱(active=True)接 SPEAKER 缆 → ERROR；已通过 test_checks_tier2.py "
                         "构造「active 音箱 + SPEAKER 连接」场景验证触发；真实清单有源/无源接线正确，零命中是合规预期",
    "LEVEL_DOMAIN": "Tier2：扬声器线缆(SPEAKER)两端非「功放/音响管理器→无源音箱」配对"
                    "（扬声器电平误入线路设备 / 线缆源自非功放）→ ERROR；"
                    "已通过 test_checks_tier2.py 构造「SPEAKER 缆接 MIXER」「SPEAKER 缆源自 SOURCE」场景验证触发；"
                    "真实清单接线正确，零命中是合规预期",
    "PHANTOM_MISSING": "Tier2：受 needs_phantom 标志门控——仅当设备声明需 P48 且上游 XLR 无 phantom 提供才报。"
                       "当前主库未给任何设备打 needs_phantom，零命中是门控未激活的预期；"
                       "已通过 test_checks_tier2.py 构造「needs_phantom 设备 + 上游无 phantom」场景验证能触发",
    "AEC_MISSING": "Tier3：受 needs_aec 标志门控——设备声明需 AEC 但系统无 aec 能力 DSP 才报 WARN；"
                   "已通过 test_checks_tier3.py 构造「needs_aec 设备 + 无 aec DSP」场景验证触发；"
                   "当前主库无 needs_aec 标记，零命中是门控未激活预期",
    "AEC_REF_UNCONNECTED": "Tier3：受 aec 能力门控——DSP 声明 aec 且远端终端存在、但参考输入口未连才报 ERROR；"
                           "已通过 test_checks_tier3.py 构造「aec DSP + vc 终端 + 参考口未连」场景验证触发；"
                           "当前主库无 aec 标记，零命中是门控未激活预期",
    "DELAY_CAPABILITY_MISSING": "Tier3：受 fill/delay_speaker 标记门控——补声音箱上游处理器无 delay 能力才报 ERROR；"
                                "已通过 test_checks_tier3.py 构造「fill 音箱 + 无 delay 处理器」场景验证触发；"
                                "当前主库无 fill 标记，零命中是门控未激活预期",
    "PTP_GM_NONE": "Tier3：受 ptp_role 参数门控——系统含 Dante 且声明了 ptp_role 但无 gm/boundary 才报 WARN；"
                   "已通过 test_checks_tier3.py 构造「ptp_role=slave 无 gm」场景验证触发；"
                   "当前主库无 ptp_role 参数，零命中是门控未激活预期",
    "CV_MIXED": "Tier3：受 line_type 字段门控——定压音箱挂低阻功放才报 ERROR；"
                "已通过 test_checks_tier3.py 构造「line_type=70v 音箱 + 低阻功放」场景验证触发；"
                "当前主库无 line_type 字段，零命中是字段缺失休眠预期",
    "CV_VOLTAGE_MISMATCH": "Tier3：受 line_type 字段门控——70V/100V 混用才报 ERROR；"
                           "已通过 test_checks_tier3.py 构造「70v 音箱 + 100v 功放」场景验证触发；"
                           "当前主库无 line_type 字段，零命中是字段缺失休眠预期",
    "XOVER_GAP": "Tier3：受 f_x 字段门控——分频点无效(<2 或非单调)才报 ERROR；"
                 "已通过 test_checks_tier3.py 构造「f_x=[1000] 单点」场景验证触发；"
                 "当前主库无 f_x 字段，零命中是字段缺失休眠预期",
    "ZONE_SINGLE_AMP": "Tier3：受 zone 字段门控——分区内音箱全挂 1 台功放才报 WARN；"
                       "已通过 test_checks_tier3.py 构造「zone=A 两音箱同 1 功放」场景验证触发；"
                       "当前主库无 zone 字段，零命中是字段缺失休眠预期",
}

# 不变式守卫：正常流程永远走不到，零命中是**预期结果**。
KNOWN_UNREACHABLE = {
    "NO_SWITCH": "build_project 保证「有 Dante 必有交换机」（清单没配就由 "
                 "_make_switches 造一台），条件恒不成立。保留它只为在交换机"
                 "逻辑被改坏时立刻报错。",
    "PORT_SIDE": "端口方向（left/right/top/bottom）只来自规格模板与主库 "
                 "ports_override，前端下拉已受限，正常数据恒合法。写错的后果"
                 "是端口坐标停在 (0,0) 飞出图纸——保留它以便立刻定位。"
                 "已构造必然违规场景（side='Bottom'）验证能正常报错。",
    "PORT_ROLE": "进出角色（in/out/io）同上。写错会让端口不参与任何配对，"
                 "图上只显示成「余量未连」，极难察觉。"
                 "已构造必然违规场景（role='input'）验证能正常报错。",
}


def main():
    hits = collections.Counter()
    levels = {}
    unknown = collections.Counter()

    for name, path, sheet in JOBS:
        try:
            entries, _ = build_entries(path, sheet=sheet)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name} 读取失败：{e}")
            continue
        if not entries:
            continue
        try:
            proj = build_project(entries, name=name)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name} 构建失败：{e}")
            continue
        validate(proj)
        by_code = collections.Counter(i.code for i in proj.issues)
        for code, n in by_code.items():
            hits[code] += n
        print(f"  · {name:<16s} 问题 {len(proj.issues):3d}  "
              + ("  ".join(f"{c}:{n}" for c, n in sorted(by_code.items()))
                 or "无"))

    codes = source_codes()
    print(f"\n{'='*74}\n校验覆盖情况（真实清单 {len(JOBS)} 份）\n{'='*74}")
    zero = []
    for code in codes:
        n = hits[code]
        if code in KNOWN_UNREACHABLE:
            flag = "· "           # 不可达守卫，零命中是预期
        elif not n and code in VERIFIED_FIRE_OK:
            flag = "✔ "           # 已人工验证能触发，只是清单合规
        elif not n:
            flag = "⚠ "           # 真正存疑：没验证过、也从没触发
        else:
            flag = "  "
        print(f"  {flag}{code:<24s} 触发 {n:5d} 次")
        if not n and code not in VERIFIED_FIRE_OK and code not in KNOWN_UNREACHABLE:
            zero.append(code)
    extra = [c for c in hits if c not in codes]
    if extra:
        print("\n  ! 出现源码里没有的 Issue 码：")
        for c in extra:
            print(f"      - {c} ×{hits[c]}")
    stale = [c for c in list(VERIFIED_FIRE_OK) + list(KNOWN_UNREACHABLE)
             if c not in codes]
    if stale:
        print("\n  ! 以下码已从 checks.py 删除，但还留在本文件的常量里，请清理：")
        for c in stale:
            print(f"      - {c}")

    if VERIFIED_FIRE_OK:
        print("\n  已人工验证（能正常触发，零命中仅因清单合规）：")
        for c, how in VERIFIED_FIRE_OK.items():
            print(f"    ✔ {c:<24s} {how}")
    if KNOWN_UNREACHABLE:
        print("\n  不变式守卫（正常流程不可达，零命中是预期）：")
        for c, why in KNOWN_UNREACHABLE.items():
            print(f"    · {c:<24s} {why}")

    if zero:
        print(f"\n⚠ 以下 {len(zero)} 类校验在所有真实清单中从未触发、且未人工验证过：")
        for c in zero:
            print(f"    - {c}")
        print("\n  两类可能：① 系统一直合规（好事）；② 该报没报（漏报）。")
        print("  区分办法：构造一个「必然违规」的最小场景喂进去，看它报不报；")
        print("  验证通过后把结论写进本文件的 VERIFIED_FIRE_OK，别重复排查。")
        return 1
    print("\n✓ 不存在「未验证且从未触发」的校验码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
