"""连线生成：相邻阶段按信号类型配对连线；Dante 经交换机；主备 failover；功放↔扬声器匹配。"""
from __future__ import annotations
from collections import defaultdict
from avcad.model.schema import (
    Signal, Redundancy, Connection, DeviceInstance,
    signal_color, signal_layer, signal_ltype,
)
from avcad.wires.amp_match import match_speakers_to_amp
from avcad.topology.chain import PROC_PRE, PROC_POST

CABLE_SIGNALS = {Signal.XLR, Signal.AES, Signal.SPEAKER, Signal.RF}
DANTE_SIGNALS = {Signal.DANTE}


def _port(inst, pid):
    for p in inst.ports:
        if p.id == pid:
            return p
    return None


def _lookup(project):
    return {i.uid: i for i in project.instances + project.switches}


def connect(project):
    """填充 project.connections（确定性）。"""
    chain = project.chain
    instances = project.instances
    by_stage = defaultdict(list)
    for i in instances:
        by_stage[i.stage].append(i)

    # 1) 相邻阶段线缆（模拟/RF/扬声器）
    for a, b in zip(chain, chain[1:]):
        if a == "AMP" and b == "SPEAKER":
            _handle_speakers(project, by_stage, a, b)
            continue
        # 无线天线链路走专用规则（级联 + 每台接收机占满天线口），不用星型配对
        if a == "ANT_DIST":
            _antenna_distribution(project, by_stage)
            continue
        if b == "ANT_DIST":
            _antennas_to_first_dist(project, by_stage)
            continue
        _generic_pair(project, by_stage.get(a, []), by_stage.get(b, []))

    # 2) 音源级（SOURCE / WIRELESS_RX）显式接入首个核心级（调音台或处理器）
    _connect_sources_to_core(project, by_stage)

    # 3) Dante 经交换机
    _dante_pass(project)

    # 4) 主备 failover 虚线
    _failover(project)

    # 5) 连线去重
    _dedup(project)


def _connect_sources_to_core(project, by_stage):
    target = None
    for c in project.chain:
        if c in (PROC_PRE, PROC_POST, "MIXER"):
            target = c
            break
    if not target:
        return
    tdevs = by_stage.get(target, [])
    if not tdevs:
        return
    for src in ("SOURCE", "WIRELESS_RX"):
        if by_stage.get(src):
            _generic_pair(project, by_stage[src], tdevs)


# ---- 无线天线链路（真分集 + 天线分配器级联） ----
# 每台分配器预留末尾 2 个出口用于级联下一台（IPS UM2000ATD：2 进 / 10 出）
CASCADE_OUTS = 2


def _rf_ports(devs, role):
    """取设备列表中指定方向的 RF 有线端口（排除 air 空中口），返回 [(dev, port)]。"""
    out = []
    for d in devs:
        for p in d.ports:
            if p.signal == Signal.RF and p.role == role and not p.air:
                out.append((d, p))
    return out


def _antennas_to_first_dist(project, by_stage):
    """外置天线 → **首台**天线分配器的输入。

    不能星型分发到所有分配器：第 2 台起的进口由上一台的级联出口供信号。
    """
    dists = by_stage.get("ANT_DIST", [])
    if not dists:
        return
    ins = _rf_ports([dists[0]], "in")
    outs = _rf_ports(by_stage.get("ANTENNA", []), "out")
    for (ad, ap), (dd, dp) in zip(outs, ins):
        project.connections.append(Connection(
            ad.uid, ap.id, dd.uid, dp.id, Signal.RF, "primary", note="天线→分配器"))


def _antenna_distribution(project, by_stage):
    """天线分配器级联 + 出口分配（阳哥 2026-08-30 确认，IPS UM2000ATD 十通道）。

    规则：
      1. 分配器按顺序串成链（BOM 顺序即链路顺序）；
      2. 非末台取**末尾 2 个出口**级联到下一台的进口；
      3. 剩余可用出口，按「每台接收机的天线口数」依次切分
         —— 真分集双通道接收机（如 UM2002）固定占 **4 口**；
      4. 末台无需级联，全部出口可用。
    """
    dists = by_stage.get("ANT_DIST", [])
    if not dists:
        return
    rxs = [d for d in project.instances if d.category == "WIRELESS_RX"]
    pending = list(rxs)

    for idx, dist in enumerate(dists):
        outs = _rf_ports([dist], "out")
        if not outs:
            continue
        is_last = (idx == len(dists) - 1)
        n_cas = 0 if is_last else min(CASCADE_OUTS, len(outs))
        # 末尾口留给级联，前面的口给接收机，读图时顺序更直观
        cascade_outs = outs[len(outs) - n_cas:] if n_cas else []
        usable = outs[: len(outs) - n_cas] if n_cas else outs

        # 级联：本台末尾出口 -> 下一台进口
        if cascade_outs and idx + 1 < len(dists):
            nxt_ins = _rf_ports([dists[idx + 1]], "in")
            for (sd, sp), (td, tp) in zip(cascade_outs, nxt_ins):
                project.connections.append(Connection(
                    sd.uid, sp.id, td.uid, tp.id, Signal.RF, "primary",
                    note="分配器级联"))

        # 接收机分配：按每台接收机实际天线口数切分可用出口
        i = 0
        while pending and i < len(usable):
            rx = pending[0]
            rins = _rf_ports([rx], "in")
            need = len(rins) or 1
            if i + need > len(usable):
                break          # 本台剩余出口不够，交给下一台分配器
            pending.pop(0)
            for k, (sd, sp) in enumerate(usable[i:i + need]):
                td, tp = rins[k]
                project.connections.append(Connection(
                    sd.uid, sp.id, td.uid, tp.id, Signal.RF, "primary",
                    note="天线分配"))
            i += need

    if pending:
        need = sum(len(_rf_ports([r], "in")) or 1 for r in pending)
        project.meta.setdefault("wireless_warnings", []).append(
            f"天线分配器出口不足：{len(pending)} 台无线接收机未分配到天线口"
            f"（尚缺 {need} 口，建议增加 UM2000ATD）")


def _dedup(project):
    seen = set()
    uniq = []
    for c in project.connections:
        key = (c.from_uid, c.from_port, c.to_uid, c.to_port)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    project.connections = uniq


def _generic_pair(project, a_devs, b_devs):
    outs, ins = [], []
    for d in a_devs:
        for p in d.ports:
            if p.role == "out" and p.signal in CABLE_SIGNALS and not p.air:
                outs.append((d, p))
    for d in b_devs:
        for p in d.ports:
            if p.role == "in" and p.signal in CABLE_SIGNALS and not p.air:
                ins.append((d, p))
    out_by_sig = defaultdict(list)
    in_by_sig = defaultdict(list)
    for d, p in outs:
        out_by_sig[p.signal].append((d, p))
    for d, p in ins:
        in_by_sig[p.signal].append((d, p))
    for sig, olist in out_by_sig.items():
        ilist = in_by_sig.get(sig, [])
        for k, (d, p) in enumerate(olist):
            if k < len(ilist):
                td, tp = ilist[k]
                # 冗余场景：共享上游设备只向主设备送模拟线，备设备通过 Dante 交换机取信号
                if td.is_backup and d.redundant_group != td.redundant_group:
                    continue
                role = _role(d, td)
                project.connections.append(Connection(
                    d.uid, p.id, td.uid, tp.id, sig, role,
                    note="" if role == "primary" else "备路径",
                ))


def _handle_speakers(project, by_stage, amp_stage, spk_stage):
    amps = by_stage.get("AMP", [])
    speakers = by_stage.get("SPEAKER", [])
    passive = [s for s in speakers if not s.active]
    active = [s for s in speakers if s.active]
    # 无源：按功放通道匹配
    if amps and passive:
        buckets = [[] for _ in amps]
        for idx, s in enumerate(passive):
            buckets[idx % len(amps)].append(s)
        for amp, grp in zip(amps, buckets):
            res = match_speakers_to_amp(amp, grp)
            spk_by_uid = {s.uid: s for s in grp}
            for ci, suids, mode, total, ok, note in res:
                if not suids:
                    continue
                amp_port = _channel_port(amp, ci)
                if amp_port is None:
                    continue
                for suid in suids:
                    spk = spk_by_uid[suid]
                    spk_port = _first_in(spk, Signal.SPEAKER)
                    if spk_port is None:
                        continue
                    role = _role(amp, spk)
                    c = Connection(amp.uid, amp_port.id, spk.uid, spk_port.id,
                                   Signal.SPEAKER, role, note=f"{mode} {total}Ω")
                    project.connections.append(c)
                    if not ok:
                        project.meta.setdefault("amp_warnings", []).append(
                            f"功放{amp.name}通道{ci+1}: {note}")
    # 有源：从前一线路级（最后一个 前置/后置处理器 > 调音台 > 扬声器管理）接入
    prev = None
    line_stages = (PROC_PRE, PROC_POST, "MIXER", "SPEAKER_MGR")
    for c in project.chain:
        if c == "SPEAKER":
            break
        if c in line_stages:
            prev = c
    if active and prev:
        prev_devs = by_stage[prev]
        pins = []
        for d in prev_devs:
            for p in d.ports:
                if p.role == "out" and p.signal in (Signal.XLR, Signal.DANTE) and not p.air:
                    pins.append((d, p))
        ains = []
        for s in active:
            for p in s.ports:
                if p.role == "in" and p.signal in (Signal.XLR, Signal.DANTE) and not p.air:
                    ains.append((s, p))
        for k, (d, p) in enumerate(pins):
            if k < len(ains):
                ts, tp = ains[k]
                project.connections.append(Connection(d.uid, p.id, ts.uid, tp.id, p.signal, _role(d, ts)))


def _dante_pass(project):
    switches = project.switches
    if not switches:
        return
    prim = switches[0]
    sec = switches[1] if len(switches) > 1 else None

    # 交换机端口按连接顺序分配，避免所有 Dante 线挤到同一个口
    used_idx = {sw.uid: 0 for sw in switches}

    def _alloc_port(sw):
        dante_ports = [p for p in sw.ports if p.signal == Signal.DANTE and not p.air]
        idx = used_idx[sw.uid]
        if idx >= len(dante_ports):
            return dante_ports[-1] if dante_ports else None
        used_idx[sw.uid] = idx + 1
        return dante_ports[idx]

    for d in project.instances:
        if d.category == "SWITCH":
            continue
        backup = d.is_backup and sec is not None
        tgt = sec if backup else prim
        for p in d.ports:
            if p.signal != Signal.DANTE or p.air:
                continue
            sp = _alloc_port(tgt)
            if sp is None:
                continue
            if p.role == "out":
                project.connections.append(Connection(
                    d.uid, p.id, tgt.uid, sp.id, Signal.DANTE,
                    "backup" if backup else "primary", note="Dante→交换机"))
            elif p.role == "in":
                project.connections.append(Connection(
                    tgt.uid, sp.id, d.uid, p.id, Signal.DANTE,
                    "backup" if backup else "primary", note="Dante←交换机"))


def _failover(project):
    """主备 failover 仅走模拟/数字音频线缆（XLR / AES）。

    注意：Dante 信号一律禁止设备间直连，统一经 _dante_pass 由交换机承载
    （阳哥要求：只要是 Dante 链路都必须直接进 Dante 交换机）。因此这里
    只取 XLR / AES 端口，绝不取 DANTE。
    """
    pairs = defaultdict(list)
    for d in project.instances:
        if d.redundant_group:
            pairs[d.redundant_group].append(d)
    for grp in pairs.values():
        if len(grp) >= 2:
            a, b = grp[0], grp[1]
            ap = _first_out(a, Signal.XLR) or _first_out(a, Signal.AES)
            bp = _first_in(b, Signal.XLR) or _first_in(b, Signal.AES)
            if ap and bp:
                project.connections.append(Connection(
                    a.uid, ap.id, b.uid, bp.id, ap.signal, "backup", note="主备failover"))


# ---- 端口查找助手 ----
def _first_out(inst, sig):
    for p in inst.ports:
        if p.role == "out" and p.signal == sig and not p.air:
            return p
    return None


def _first_in(inst, sig):
    for p in inst.ports:
        if p.role == "in" and p.signal == sig and not p.air:
            return p
    return None


def _channel_port(inst, ci):
    spk = [p for p in inst.ports if p.signal == Signal.SPEAKER and p.role == "out"]
    if ci < len(spk):
        return spk[ci]
    return spk[0] if spk else None


def _role(a, b):
    return "backup" if (a.is_backup or b.is_backup) else "primary"
