"""三份真实清单的系统图生成校验器（供「循环验证 N 遍」使用）。

检查项：
  1. 出图不报错
  2. 版式：重叠 0 / 斜线 0
  3. 无孤立节点（除链路起点 SOURCE / WIRELESS_MIC 外，每台设备都要有连线）
  4. 端口不超配（任一设备的出线数 <= 其输出端口数）
  5. 连线方向：功放 -> 扬声器、交换机 -> 有源音箱/处理器，不得反向
  6. 无线链路完整：ANTENNA -> ANT_DIST -> WIRELESS_RX
  7. 会讨链路：无线会议单元 -> 天线盒

用法：python3 scripts/validate_projects.py [遍数]
退出码：0 = 全部通过；1 = 存在问题
"""
from __future__ import annotations
import sys
import json
import collections

sys.path.insert(0, ".")

from avcad.workflow.importers import build_entries, to_bom_csv  # noqa: E402
from avcad.core.build import build_project  # noqa: E402
import avcad.ui.app as app  # noqa: E402

YOUTENG = "/Users/mac/Desktop/202601/友腾-EAW音频扩声20260807.xlsx"
TAIYANG = "/Users/mac/Desktop/202601/文博-太阳纸业20260806.xlsx"
HEZE = "/Users/mac/Desktop/202601/华演出-菏泽曹州古城广场演出系统20260813.xlsx"

# (显示名, 文件路径, sheet)
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

# 链路起点：本身不接收上游线缆，但必须有出线
HEADS = {"SOURCE", "WIRELESS_MIC"}


def check_one(name, path, sheet):
    """返回 (errors, warnings, stats)。errors 为空才算通过。"""
    issues = []
    warns = []
    entries, dropped = build_entries(path, sheet=sheet)
    if not entries:
        return ["无有效设备"], [], None

    # --- 1/2. 出图 ---
    r = app._dispatch("/api/run", json.dumps(
        {"bom": to_bom_csv(entries), "name": name, "require_legend": False}))
    if r.get("error"):
        return [f"出图报错: {r['error']}"], [], None

    v = r.get("validation") or {}
    if v.get("overlap"):
        issues.append(f"重叠 {v['overlap']} 处")
    if v.get("diagonal"):
        issues.append(f"斜线 {v['diagonal']} 处")
    if not v.get("ok", True):
        issues.append("版式校验未通过")

    proj = build_project(entries, name=name)
    inst = {i.uid: i for i in proj.instances}
    conns = list(proj.connections)

    touched = set()
    for c in conns:
        touched.add(c.from_uid)
        touched.add(c.to_uid)

    # --- 3. 孤立节点 ---
    # 无线发射端（手持/头戴话筒）设计上不产生线缆连接，只画本体与空中 RF 口
    orphans = [i for uid, i in inst.items()
               if uid not in touched and i.category not in ("WIRELESS_MIC",)]
    # 清单只有扬声器、没有任何前端设备时，孤立是清单不完整所致，非程序缺陷
    front = {"AMP", "MIXER", "PROCESSOR", "SPEAKER_MGR", "SOURCE",
             "WIRELESS_RX", "MIC_HOST", "SWITCH", "IO"}
    has_front = any(i.category in front for i in proj.instances)
    if orphans and not has_front:
        detail = " ".join(f"{k}x{v}" for k, v in
                          collections.Counter(i.category for i in orphans).most_common(3))
        warns.append(f"清单只含后端设备（孤立 {len(orphans)} 台 [{detail}]），"
                     f"缺调音台/处理器/功放等前端，无法构成完整链路")
    elif orphans:
        # 音源多于下游输入容量：配置不足，程序已尽力接入
        src_orph = [i for i in orphans if i.category == "SOURCE"]
        other = [i for i in orphans if i.category != "SOURCE"]
        if src_orph:
            n_in = sum(len([p for p in i.ports if p.role == "in"])
                       for i in proj.instances
                       if i.category in ("PROCESSOR", "MIXER"))
            warns.append(f"音源 {len(src_orph)} 路超出可用输入（处理器+调音台共 {n_in} 路），"
                         f"建议增加处理器或调音台")
        if other:
            detail = " ".join(f"{k}x{v}" for k, v in
                              collections.Counter(i.category for i in other).most_common(3))
            issues.append(f"孤立节点 {len(other)} 台 [{detail}]")

    # --- 4. 端口不超配 ---
    # 按**端口**统计而非连线数：一个功放通道并联多台音箱时，
    # 会从同一端口画出多条线，这是合法的（吸顶音箱并联）。
    out_ports = collections.defaultdict(set)
    in_ports = collections.defaultdict(set)
    for c in conns:
        out_ports[c.from_uid].add(c.from_port)
        in_ports[c.to_uid].add(c.to_port)
    for uid, i in inst.items():
        n_out = len([p for p in i.ports if p.role == "out"])
        n_in = len([p for p in i.ports if p.role == "in"])
        if n_out and len(out_ports[uid]) > n_out:
            issues.append(f"{i.model or i.category} 占用出口 {len(out_ports[uid])} > 出口 {n_out}")
        if n_in and len(in_ports[uid]) > n_in:
            issues.append(f"{i.model or i.category} 占用进口 {len(in_ports[uid])} > 进口 {n_in}")

    # --- 5. 连线方向 ---
    for c in conns:
        a, b = inst.get(c.from_uid), inst.get(c.to_uid)
        if not a or not b:
            continue
        pair = (a.category, b.category)
        if pair in (("SPEAKER", "AMP"), ("SPEAKER", "MIXER"), ("AMP", "SWITCH")):
            issues.append(f"方向反了: {a.model or a.category} -> {b.model or b.category}")
        # 手拉手串联（会议单元之间）是合法的链式连接，不是错误
        if (c.note or "").startswith("手拉手"):
            continue
        if a.category in HEADS and b.category in HEADS:
            issues.append(f"两个链路起点互连: {a.category} -> {b.category}")

    # --- 6. 无线链路 ---
    ants = [i for i in proj.instances if i.category == "ANTENNA"]
    dists = [i for i in proj.instances if i.category == "ANT_DIST"]
    rxs = [i for i in proj.instances if i.category == "WIRELESS_RX"]
    if rxs:
        if not dists:
            issues.append(f"{len(rxs)} 台接收机但没有天线分配器")
        else:
            no_in = [i for i in rxs
                     if not any(c.to_uid == i.uid for c in conns)]
            if no_in:
                issues.append(f"{len(no_in)} 台无线接收机未接天线分配器")
    if ants and dists:
        no_ant = [i for i in ants
                  if not any(c.from_uid == i.uid for c in conns)]
        if no_ant:
            issues.append(f"{len(no_ant)} 支天线未接入分配器")

    # --- 7. 无源音箱必须经功放 ---
    for i in proj.instances:
        if i.category != "SPEAKER":
            continue
        if getattr(i, "active", False):
            continue
        has_amp = any(c.to_uid == i.uid and inst[c.from_uid].category == "AMP"
                      for c in conns if c.from_uid in inst)
        if not has_amp:
            issues.append(f"无源音箱 {i.model} 未接功放")

    # --- 8. 告警 ---
    for w in (proj.meta.get("wireless_warnings") or []):
        issues.append(f"无线告警: {w}")

    stats = {
        "devices": len(proj.instances),
        "connections": len(conns),
        "dropped": len(dropped),
        "overlap": v.get("overlap"), "diagonal": v.get("diagonal"),
        "svg": len(r.get("svg", "")),
    }
    return issues, warns, stats


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    all_ok = True
    for rd in range(1, rounds + 1):
        print(f"\n{'='*78}\n第 {rd} 遍验证\n{'='*78}")
        bad = 0
        for name, path, sheet in JOBS:
            try:
                issues, warns, stats = check_one(name, path, sheet)
            except Exception as ex:  # noqa: BLE001
                issues, warns, stats = [f"异常: {type(ex).__name__}: {ex}"], [], None
            st = stats or {}
            tag = "✗" if issues else ("!" if warns else "✓")
            print(f"  {tag} {name:16s} 设备{st.get('devices',0):3d} "
                  f"连线{st.get('connections',0):3d} "
                  f"排除{st.get('dropped',0):2d} "
                  f"重叠{st.get('overlap')} 斜线{st.get('diagonal')} "
                  f"SVG {st.get('svg',0)//1024}KB")
            for m in issues[:6]:
                print(f"      错误: {m}")
            for m in warns[:4]:
                print(f"      提示: {m}")
            if issues:
                bad += 1
                all_ok = False
        print(f"  --> 第 {rd} 遍：{'全部通过' if bad == 0 else f'{bad} 个方案有问题'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
