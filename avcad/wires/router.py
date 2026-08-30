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

    # 0) 会讨手拉手优先：会议单元串到主机后，由主机统一汇出，
    #    不再走 SOURCE 的星型配对（否则会画成「每台话筒直连处理器」）。
    conf_units = _conference_link(project, by_stage)
    if conf_units:
        by_stage["SOURCE"] = [i for i in by_stage.get("SOURCE", [])
                              if i.uid not in conf_units]

    # 1) 相邻阶段线缆（模拟/RF/扬声器）
    for a, b in zip(chain, chain[1:]):
        if b == "SPEAKER":
            # 只要下游是扬声器就拆开处理：
            #   有源音箱 → _handle_speakers（不经过管理器、只取一路信号）；
            #   无源音箱 → 仍走通用相邻级配对（管理器 / 功放 → SPK 口）。
            # 之前只有「功放→扬声器」才走专用规则，导致无功放级的方案里
            # 有源音箱被通用配对连上模拟线、同时 _dante_pass 又补 Dante，
            # 一台音箱两根线（EAW2：2 路 XLR + 14 路 Dante 混接）。
            _handle_speakers(project, by_stage, a, b)
            # 功放→扬声器的匹配已在上面完成，别让通用配对再连一遍
            fed = {c.to_uid for c in project.connections
                   if c.signal == Signal.SPEAKER}
            _generic_pair(project, by_stage.get(a, []),
                          [s for s in by_stage.get(b, [])
                           if not s.active and s.uid not in fed])
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

    # 5) 末位救援：仍孤立的音源接到空闲进口（话筒多于处理器输入路数时）
    _orphan_sources_rescue(project, by_stage)

    # 6) 连线去重
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
# 默认级联预留出口数（仅当型号未显式声明 params.cascade_outs 时使用）
CASCADE_OUTS = 2


def _cascade_outs(dist):
    """取该分配器的级联预留出口数。

    只有明确支持级联的型号才 >0：
      - IPS UM2000ATD = 2（官网：「级联端口能够以链式形式连接多套天线分配系统」）
      - AUDIX ADS48   = 0（官方资料只说「合并 4 套系统」，未提级联）
    """
    try:
        v = int((dist.params or {}).get("cascade_outs", CASCADE_OUTS))
    except (TypeError, ValueError):
        v = CASCADE_OUTS
    return max(0, v)


def required_dist_count(n_rx, antennas_per_rx=4, outputs=10, cascade=CASCADE_OUTS):
    """带 n_rx 台接收机所需的最少天线分配器台数。

    容量模型与 _antenna_distribution 一致：
      非末台可用出口 = outputs − cascade；末台可用出口 = outputs；
      每台接收机占 antennas_per_rx 个出口。
    """
    if n_rx <= 0:
        return 0
    per_rx = max(1, int(antennas_per_rx or 1))
    n = 1
    while n <= 200:
        cap = 0
        for i in range(n):
            usable = outputs if i == n - 1 else max(0, outputs - cascade)
            cap += usable // per_rx
        if cap >= n_rx:
            return n
        n += 1
    return n


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
        n_cas = 0 if is_last else min(_cascade_outs(dist), len(outs))
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

    # 容量测算：给出「带这些接收机实际需要几台分配器」的提示
    if rxs:
        per_rx = max(len(_rf_ports([r], "in")) or 1 for r in rxs)
        outs = max((len(_rf_ports([d], "out")) for d in dists), default=0)
        cas = _cascade_outs(dists[0]) if dists else CASCADE_OUTS
        req = required_dist_count(len(rxs), per_rx, outs, cas)
        project.meta["wireless_plan"] = {
            "receivers": len(rxs),
            "antennas_per_receiver": per_rx,
            "dists": len(dists),
            "dists_required": req,
            "outputs_per_dist": outs,
            "ok": len(dists) >= req,
        }
        if len(dists) < req:
            project.meta.setdefault("wireless_warnings", []).append(
                f"天线分配器数量不足：{len(rxs)} 台接收机至少需要 {req} 台"
                f"（当前 {len(dists)} 台）")


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
        # 按各功放的**通道容量加权**分配，而不是简单取模轮询：
        # 两通道与四通道功放混用时，轮询会让小功放超载。
        # 通道不够时由 match_speakers_to_amp 在通道内并联（吸顶音箱常见）。
        caps = [max(1, len([p for p in a.ports
                            if p.role == "out" and p.signal == Signal.SPEAKER]))
                for a in amps]
        total_cap = sum(caps)
        n = len(passive)
        buckets = [[] for _ in amps]
        quota = [max(1, round(n * c / total_cap)) for c in caps]
        idx = 0
        for s in passive:
            # 找还没满额且剩余额度最多的功放
            order = sorted(range(len(amps)),
                           key=lambda k: (quota[k] - len(buckets[k])), reverse=True)
            for k in order:
                if len(buckets[k]) < quota[k]:
                    buckets[k].append(s)
                    idx += 1
                    break
            else:
                buckets[order[0]].append(s)
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
    # 有源：从前一线路级接入。
    # ★ 阳哥规则 2026-08-30：**有源音箱不经过音响管理器**——
    #   自带功放的音箱直接从调音台/处理器取信号，SPEAKER_MGR 只服务
    #   「处理器 -> 管理器 -> 功放 -> 无源音箱」这条链。
    prev = None
    line_stages = (PROC_PRE, PROC_POST, "MIXER", "SWITCH", "SPEAKER_MGR")
    for c in project.chain:
        if c == "SPEAKER":
            break
        if c in line_stages:
            prev = c
    # 若只剩音响管理器（常见于纯有源系统），改从调音台/处理器直接取
    if prev == "SPEAKER_MGR":
        for c in ("MIXER", PROC_POST, PROC_PRE):
            if c in project.chain and by_stage.get(c):
                prev = c
                break
    if active and prev:
        prev_devs = by_stage[prev]
        # 前级出口可能已被相邻级配对占用（如调音台 OUT1~4 已给音响管理器）。
        # 有源音箱只能接**空闲**出口，否则同一端口被复用：图上看着线数没变，
        # 实际是两根线压在同一个口上（阳哥 2026-08-30：「调音台并没有增加输出的线」）。
        used_out = {(c.from_uid, c.from_port) for c in project.connections
                    if c.signal in (Signal.XLR, Signal.AES)}
        # Dante 一律禁止设备间直连，统一由 _dante_pass 经交换机承载，
        # 所以这里只取模拟/数字音频出口（XLR / AES）。
        pins = []
        for d in prev_devs:
            for p in d.ports:
                if (p.role == "out" and p.signal in (Signal.XLR, Signal.AES)
                        and not p.air and (d.uid, p.id) not in used_out):
                    pins.append((d, p))
        # 每台有源音箱只取**一路**信号。
        analog_spk, dante_spk = [], []
        for s in active:
            ap = next((p for p in s.ports
                       if p.role == "in" and p.signal in (Signal.XLR, Signal.AES)
                       and not p.air), None)
            dp = next((p for p in s.ports
                       if p.role == "in" and p.signal == Signal.DANTE
                       and not p.air), None)
            if ap:
                analog_spk.append((s, ap))
            if dp:
                dante_spk.append((s, dp))

        # ★ 同一方案里有源音箱的取信号方式要**统一**，不要一半模拟一半 Dante：
        #   空闲模拟出口够喂全部有源音箱 → 全走模拟；
        #   不够、但每台都能走 Dante 且有交换机 → 全部交给 _dante_pass 走 Dante；
        #   否则能走多少模拟走多少，剩下的由 _dante_pass 补 Dante。
        if (len(pins) < len(analog_spk) and project.switches
                and len(dante_spk) == len(active)):
            return

        for s, sp in analog_spk:
            if not pins:
                break
            # 优先同信号类型的出口（XLR→XLR、AES→AES）
            k = next((i for i, (_d, _p) in enumerate(pins)
                      if _p.signal == sp.signal), 0)
            d, p = pins.pop(k)
            project.connections.append(
                Connection(d.uid, p.id, s.uid, sp.id, sp.signal, _role(d, s)))


def _orphan_sources_rescue(project, by_stage):
    """末位救援：常规配对后仍孤立的音源，接到还有空闲进口的处理/混音设备。

    真实清单常见「话筒数 > 处理器输入路数」（如太阳纸业 4F：27 支话筒 vs
    2 台 GMN1208D 共 24 路模拟输入）。多余话筒应直入调音台的**空闲**通道。

    ★ 必须在所有常规连线之后调用：先让处理器占满调音台输入，
      再用剩下的空口接溢出音源，否则会把调音台挤爆（进线数 > 进口数）。
    """
    used_in = {(c.to_uid, c.to_port) for c in project.connections}
    linked = {c.from_uid for c in project.connections}

    orphans = []
    for d in by_stage.get("SOURCE", []):
        if d.uid in linked:
            continue
        p = next((p for p in d.ports
                  if p.role == "out" and p.signal in CABLE_SIGNALS and not p.air), None)
        if p:
            orphans.append((d, p))
    if not orphans:
        return

    free = []
    for st in ("PROC_PRE", "PROCESSOR", "MIXER"):
        for d in by_stage.get(st, []):
            for p in d.ports:
                if (p.role == "in" and p.signal in CABLE_SIGNALS and not p.air
                        and (d.uid, p.id) not in used_in):
                    free.append((d, p))

    for d, p in orphans:
        for k, (td, tp) in enumerate(free):
            if tp.signal != p.signal:
                continue
            role = _role(d, td)
            project.connections.append(Connection(
                d.uid, p.id, td.uid, tp.id, p.signal, role,
                note="音源直入" if td.category == "MIXER" else ""))
            free.pop(k)
            break


def _conference_link(project, by_stage):
    """会讨手拉手：有线会议单元用六芯 T 型线串联后接入会议主机。

    官方（ezpro CF63 系列）：「会议主机与有线会议单元、天线盒均采用专用六芯
    主缆连接」「有线系统单元间采用 T 型线连接」「支持有线单元环形手拉手连接，
    某台单元故障不影响整套系统工作」。

    主库侧约定：会议单元 ``params.host`` 指向主机型号（如 CF6300），
    且带一对 DIN in/out（T 型线）。本函数按主机进口数把单元分成若干条链，
    链内首尾相接，链尾接主机的一路输入。

    返回已接线的单元 uid 集合——这些单元不应再参与 SOURCE 的星型配对。
    """
    hosts = [i for i in project.instances if i.category == "MIC_HOST"]
    if not hosts:
        return set()
    units = [i for i in project.instances
             if (getattr(i, "params", None) or {}).get("host")]
    if not units:
        return set()

    host = hosts[0]
    ins = [p for p in host.ports if p.role == "in"]
    if not ins:
        return set()

    chains = [[] for _ in ins]
    for idx, u in enumerate(units):
        chains[idx % len(chains)].append(u)

    wired = set()
    for k, ch in enumerate(chains):
        if not ch:
            continue
        prev = None
        for u in ch:
            u_in = next((p for p in u.ports if p.role == "in"), None)
            if prev is not None and u_in is not None:
                p_out = next((p for p in prev.ports if p.role == "out"), None)
                if p_out:
                    project.connections.append(Connection(
                        prev.uid, p_out.id, u.uid, u_in.id, Signal.XLR,
                        "primary", note="手拉手"))
                    wired.add(prev.uid)
            prev = u
        # 链尾 -> 主机
        last_out = next((p for p in prev.ports if p.role == "out"), None)
        if last_out:
            project.connections.append(Connection(
                prev.uid, last_out.id, host.uid, ins[k].id, Signal.XLR,
                "primary", note="手拉手→主机"))
            wired.add(prev.uid)
    return wired


def _dante_pass(project):
    switches = project.switches
    if not switches:
        return
    prim = switches[0]
    sec = switches[1] if len(switches) > 1 else None

    # 交换机端口按连接顺序分配，避免所有 Dante 线挤到同一个口
    used_idx = {sw.uid: 0 for sw in switches}
    # 已级联过的交换机（用于避免重复画级联线）
    cascaded = set()

    def _first_free(prefer=None):
        """返回 (交换机, 端口)。优先用 prefer，端口用尽后顺延到下一台。"""
        order = ([prefer] if prefer else []) + \
                [s for s in switches if s is not prefer]
        for sw in order:
            if sw is None:
                continue
            dante_ports = [p for p in sw.ports
                           if p.signal == Signal.DANTE and not p.air]
            idx = used_idx[sw.uid]
            if idx < len(dante_ports):
                used_idx[sw.uid] = idx + 1
                return sw, dante_ports[idx]
        return None, None

    def _cascade_to(sw, backup):
        """首次用到非首选交换机时，画一条「上级交换机 → 本机」的级联线。"""
        if sw is None or sw is prim or sw.uid in cascaded:
            return
        cascaded.add(sw.uid)
        up, up_port = _first_free(prim)
        if up is None:
            return
        my_ports = [p for p in sw.ports
                    if p.signal == Signal.DANTE and not p.air]
        if not my_ports:
            return
        project.connections.append(Connection(
            up.uid, up_port.id, sw.uid, my_ports[-1].id, Signal.DANTE,
            "backup" if backup else "primary", note="交换机级联"))

    for d in project.instances:
        if d.category == "SWITCH":
            continue
        # ★ 终端扬声器只取一路信号：模拟已经喂上了（调音台/处理器直供有源音箱），
        #   就不再画 Dante 线。否则每台有源音箱会多出一条横向的 Dante 线
        #   （阳哥 2026-08-30：太阳纸业 1F「横向的线多了两根」）。
        #   注意只针对 SPEAKER——处理器/调音台跨模拟与 Dante 两个域是正常设计。
        if d.category == "SPEAKER" and _has_analog_in(project, d):
            continue
        backup = d.is_backup and sec is not None
        prefer = sec if backup else prim
        for p in d.ports:
            if p.signal != Signal.DANTE or p.air:
                continue
            tgt, sp = _first_free(prefer)
            if sp is None:
                continue
            if tgt is not prim:
                _cascade_to(tgt, backup)
            if p.role == "out":
                project.connections.append(Connection(
                    d.uid, p.id, tgt.uid, sp.id, Signal.DANTE,
                    "backup" if backup else "primary", note="Dante→交换机"))
            elif p.role == "in":
                project.connections.append(Connection(
                    tgt.uid, sp.id, d.uid, p.id, Signal.DANTE,
                    "backup" if backup else "primary", note="Dante←交换机"))

    # 清单配了多台交换机但 Dante 设备不多时，空闲交换机会变成孤立节点。
    # 工程上多交换机必然成链（堆叠/级联），这里补上，避免图上有悬空设备。
    for sw in switches[1:]:
        if sw.uid not in cascaded and used_idx.get(sw.uid, 0) == 0:
            _cascade_to(sw, False)


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


def _has_analog_in(project, dev):
    """该设备是否已经接到了模拟/数字音频进线（XLR / AES）。"""
    return any(c.to_uid == dev.uid and c.signal in (Signal.XLR, Signal.AES)
               for c in project.connections)


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
