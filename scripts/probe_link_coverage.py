"""连线覆盖度探针：统计每个连线函数在真实清单里实际产生了多少条连线。

背景（2026-08-31）：会讨天线盒链路因为三份真实清单里都没有 CF6300WB/CF6350，
**从未被走到**——结果藏了 1 处致命崩溃 + 3 处画错。而当时 10 方案 × 8 遍
验证是全绿的。教训：**真实清单全绿 ≠ 代码正确，只说明这条路径没被走到。**

本脚本给 `wires/router.py` 里所有会产出连线的函数装计数器，跑完 10 份真实
清单后列出各函数的命中情况。**零命中的函数就是风险区**，需要构造样例场景
单独人工核对（参考 `scripts/render_conference_box_demo.py` 的做法）。

用法：python3 scripts/probe_link_coverage.py   （或 scripts/probe_all.py 跑全部）
退出码：0 = 不存在「零命中且未登记」的函数；1 = 存在（仅提示，非测试失败）

已知零命中函数与它们的回归测试（2026-08-31 补齐，别再重复排查）：
    _conference_box_link  → avcad/tests/test_conference_box.py
    _mixer_cascade        → avcad/tests/test_mixer_cascade.py
    _orphan_sources_rescue → avcad/tests/test_redundancy_failover.py
    _failover             → avcad/tests/test_redundancy_failover.py
零命中本身不是缺陷，只是说明**必须靠构造场景测**，真实清单覆盖不到。
"""
from __future__ import annotations
import sys
import collections

sys.path.insert(0, ".")

import avcad.wires.router as router  # noqa: E402
from avcad.workflow.importers import build_entries, to_bom_csv  # noqa: E402
from avcad.core.build import build_project  # noqa: E402

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

# 会往 project.connections 里追加连线的函数（含内部被多次调用的通用配对）
LINK_FUNCS = [
    "_conference_link",
    "_conference_box_link",
    "_mixer_cascade",
    "_antennas_to_first_dist",
    "_antenna_distribution",
    "_connect_sources_to_core",
    "_generic_pair",
    "_handle_speakers",
    "_orphan_sources_rescue",
    "_dante_pass",
    "_failover",
]

# 只做辅助、本身不产连线，但需要被执行到才有意义（统计调用次数而非连线数）
CALL_ONLY = ["_dedup"]

# 已确认「真实清单永远走不到」、但已用构造场景补了回归测试的函数。
# 与 probe_issue_coverage 的 VERIFIED_FIRE_OK 同理：零命中是**预期结果**，
# 不算待处置项，别再重复排查。新增连线函数若零命中且未登记到这里，
# 探针会报 ⚠ 并退出 1。
KNOWN_ZERO_HIT = {
    "_conference_box_link": "avcad/tests/test_conference_box.py",
    "_mixer_cascade": "avcad/tests/test_mixer_cascade.py",
    "_orphan_sources_rescue": "avcad/tests/test_redundancy_failover.py",
    "_failover": "avcad/tests/test_redundancy_failover.py",
}

hits = collections.defaultdict(lambda: {"calls": 0, "links": 0})
_orig = {}


def _install():
    """给每个目标函数套壳：记录调用次数与调用期间新增的连线数。"""
    for fname in LINK_FUNCS + CALL_ONLY:
        fn = getattr(router, fname, None)
        if fn is None:
            print(f"  ! router 里找不到 {fname}，跳过")
            continue
        _orig[fname] = fn

        def make(name, orig):
            def wrapper(*a, **kw):
                proj = a[0] if a else None
                before = len(getattr(proj, "connections", [])) if proj else 0
                hits[name]["calls"] += 1
                try:
                    return orig(*a, **kw)
                finally:
                    after = len(getattr(proj, "connections", [])) if proj else 0
                    hits[name]["links"] += max(0, after - before)
            return wrapper

        setattr(router, fname, make(fname, fn))


def main():
    _install()
    for name, path, sheet in JOBS:
        try:
            entries, _ = build_entries(path, sheet=sheet)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name} 读取失败：{e}")
            continue
        if not entries:
            print(f"  ! {name} 无有效设备")
            continue
        try:
            proj = build_project(entries, name=name)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name} 构建失败：{e}")
            continue
        print(f"  · {name:<16s} 设备 {len(proj.instances):3d} "
              f"连线 {len(proj.connections):3d}")

    print(f"\n{'='*74}\n连线函数覆盖情况（真实清单 {len(JOBS)} 份）\n{'='*74}")
    zero = []
    known = []
    for fname in LINK_FUNCS:
        h = hits[fname]
        if h["links"]:
            flag = "  "
        elif fname in KNOWN_ZERO_HIT:
            flag = "✔ "            # 零命中是预期，已补构造场景回归测试
            known.append(fname)
        else:
            flag = "⚠ "            # 零命中且没登记 → 真正的风险区
            zero.append(fname)
        print(f"  {flag}{fname:<28s} 调用 {h['calls']:5d} 次  "
              f"产出连线 {h['links']:5d} 条")
    print(f"\n  （辅助）" + "  ".join(
        f"{f}:{hits[f]['calls']}次" for f in CALL_ONLY))

    if known:
        print(f"\n  已知零命中（真实清单走不到，已补构造场景回归测试）：")
        for f in known:
            print(f"    ✔ {f:<28s} {KNOWN_ZERO_HIT[f]}")

    if zero:
        print(f"\n⚠ 以下 {len(zero)} 个连线函数零命中、且未登记到 KNOWN_ZERO_HIT：")
        for f in zero:
            print(f"    - {f}")
        print("\n  这些分支从未被真实数据覆盖，缺陷可能潜伏其中。")
        print("  建议：构造样例场景单独人工核对"
              "（参见 scripts/render_conference_box_demo.py），")
        print("  补好回归测试后把函数名登记进 KNOWN_ZERO_HIT，别重复排查。")
        return 1
    print("\n✓ 不存在「零命中且未登记」的连线函数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
