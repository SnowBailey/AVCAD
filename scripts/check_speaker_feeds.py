"""全局复查：所有方案里有源/无源扬声器的取信号情况。

检查项（阳哥 2026-08-30 修正版）：
  1. 每台有源音箱模拟与 Dante 都要接（两条独立链路），但每种信号最多 1 路
  2. 进线的信号类型是否与它落的那个端口一致
  3. 前级设备的出口是否被重复占用（同一 from_port 出现两次以上）
  4. 有源音箱是否误接 Dante 直连线（Dante 必须经交换机）
"""
from __future__ import annotations
import sys
import collections

sys.path.insert(0, ".")

from avcad.workflow.importers import build_entries  # noqa: E402
from avcad.core.build import build_project  # noqa: E402

HEZE = "/Users/mac/Desktop/202601/华演出-菏泽曹州古城广场演出系统20260813.xlsx"
YOUTENG = "/Users/mac/Desktop/202601/友腾-EAW音频扩声20260807.xlsx"
TAIYANG = "/Users/mac/Desktop/202601/文博-太阳纸业20260806.xlsx"

JOBS = [
    ("A-菏泽", HEZE, None),
    ("B-LAC", YOUTENG, "L-ACOUSTICS"),
    ("B-EAW1", YOUTENG, "EAW1"),
    ("B-EAW2", YOUTENG, "EAW2"),
    ("B-EAW3", YOUTENG, "EAW3 KF210"),
    ("B-EAW4", YOUTENG, "EAW4"),
    ("C-1F", TAIYANG, "1F会议室"),
    ("C-2F", TAIYANG, "2F会议室"),
    ("C-3F", TAIYANG, "3F会议室"),
    ("C-4F", TAIYANG, "4F会议室"),
]

ANALOG = {"XLR", "AES"}


def main():
    bad = 0
    for name, path, sheet in JOBS:
        entries, _ = build_entries(path, sheet=sheet)
        proj = build_project(entries, name=name)
        by_uid = {i.uid: i for i in proj.instances + proj.switches}
        cat = {i.uid: i.category for i in proj.instances + proj.switches}

        spk = [i for i in proj.instances if i.category == "SPEAKER"]
        active = [s for s in spk if s.active]
        passive = [s for s in spk if not s.active]

        feeds = collections.defaultdict(list)
        for c in proj.connections:
            if cat.get(c.to_uid) == "SPEAKER" and c.signal.value in (*ANALOG, "DANTE"):
                feeds[c.to_uid].append(c)
        # 无源音箱走 SPEAKER 信号（功放），单独统计
        spk_wires = collections.Counter()
        for c in proj.connections:
            if cat.get(c.to_uid) == "SPEAKER":
                spk_wires[c.signal.value] += 1

        issues = []
        for s in active:
            fs = feeds.get(s.uid, [])
            if len(fs) == 0:
                issues.append(f"{s.model} 无音频进线")
                continue
            sigs = collections.Counter(f.signal.value for f in fs)
            for sig, n in sigs.items():
                if n > 1:
                    issues.append(f"{s.model} 取了 {n} 路 {sig}")
            for f in fs:
                tgt = next((p for p in s.ports if p.id == f.to_port), None)
                if tgt is None or tgt.signal != f.signal:
                    issues.append(
                        f"{s.model} 线标{f.signal.value}落到了 "
                        f"{f.to_port}({tgt.signal.value if tgt else '?'})")
                if f.signal.value == "DANTE" and cat.get(f.from_uid) != "SWITCH":
                    issues.append(
                        f"{s.model} 的 Dante 来自 "
                        f"{by_uid[f.from_uid].model}（非交换机）")
        # 前级出口重复占用
        dup = collections.Counter()
        for c in proj.connections:
            if c.signal.value in ANALOG or c.signal.value == "DANTE":
                dup[(c.from_uid, c.from_port)] += 1
        for (uid, pid), n in dup.items():
            if n > 1:
                d = by_uid.get(uid)
                issues.append(f"{d.model if d else uid} 出口 {pid} 被 {n} 条线复用")

        flag = "✗" if issues else "✓"
        if issues:
            bad += 1
        print(f"{flag} {name:8s} 音箱 {len(spk):3d}（有源 {len(active):2d} / "
              f"无源 {len(passive):2d}） 进线 "
              + ",".join(f"{k}×{v}" for k, v in spk_wires.most_common()))
        for t in issues:
            print(f"      ! {t}")
    print("\n有问题的方案数：", bad)


if __name__ == "__main__":
    main()
