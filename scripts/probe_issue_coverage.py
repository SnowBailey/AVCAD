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


def source_codes():
    """从 checks.py 源码里正则提取全部 Issue 码。

    ★ 此前这里是硬编码名单（KNOWN_CODES），注释还写着「新增校验时记得同步这里」——
      结果 PORT_SIDE / PORT_ROLE 加进 checks.py 后没同步，两个码直接**从探针
      视野里消失**，探针依旧报告「不存在未验证的校验码」。一个用来发现
      「名单没跟上枚举」的工具，自己倒在了同一个坑里。
      改成从源码提取后，新增校验自动进入统计，漏登记会立刻被报为存疑。
    """
    src = open(CHECKS_PY, encoding="utf-8").read()
    found = re.findall(r'Issue\(\s*["\'][A-Z]+["\']\s*,\s*["\']([A-Z_]+)["\']', src)
    assert found, (
        f"从 {CHECKS_PY} 里没提取到任何 Issue 码——Issue(...) 的写法变了？"
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
    "SPOF": "单交换机 + 备机带 Dante → WARN 正常报",
    "PAIR_MISSING": "pair 指向不存在的 uid → ERROR 正常报",
    "PAIR_TYPE": "主备跨类别配对 → ERROR 正常报",
    "REDUNDANCY": "同类只有 1 台却标冗余 → WARN 正常报",
    "LINK_BACKUP_NO_DANTE": "标链路冗余但无 Dante 设备 → WARN 正常报",
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
