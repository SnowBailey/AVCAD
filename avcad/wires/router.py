"""连线生成：相邻阶段按信号类型配对连线；Dante 经交换机；主备 failover；功放↔扬声器匹配。"""
from __future__ import annotations
from collections import defaultdict
from avcad.model.schema import (
    Signal, Redundancy, Connection, DeviceInstance,
    signal_color, signal_layer, signal_ltype, redundancy_scope,
)
from avcad.model import category_kb
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
    # 输入口占用表：(to_uid, to_port) → 占用标签。所有「向输入口落线」的助手都经
    # _claim_input 占用，从源头拦截「两个不同信号源接入同一输入口」的物理不可能
    # （_dedup 只按完整 from/to 元组去重，抓不到「不同源 → 同一输入口」）。
    project._used_in = {}

    # 0) 会讨手拉手优先：会议单元串到主机后，由主机统一汇出，
    #    不再走 SOURCE 的星型配对（否则会画成「每台话筒直连处理器」）。
    conf_units = _conference_link(project, by_stage)
    _conference_box_link(project, by_stage, conf_units)
    if conf_units:
        by_stage["SOURCE"] = [i for i in by_stage.get("SOURCE", [])
                              if i.uid not in conf_units]

    # 0.5) 可级联调音台（如 IPS AM860 自动混音器）用 LINK 口串成扩展总线。
    #      从机的 OUT 不参与后级连线（信号已随 LINK 汇总到主机）。
    cascade_slaves = _mixer_cascade(project)

    # 0.8) 天线合路器优先：天线(OUT) → 合路器(IN)（专属规则，避免被相邻 stage
    #      通用配对在 (ANTENNA, ANT_COMBINE) 上重复连线后被 _dedup 覆盖掉正确 note）。
    _wire_antennas_to_combiner(project, by_stage)

    # 1) 相邻阶段线缆（模拟/RF/扬声器）
    for a, b in zip(chain, chain[1:]):
        if b == "SPEAKER":
            # 只要下游是扬声器就拆开处理：
            #   有源音箱 → _handle_speakers（不经过管理器，模拟接前级空闲出口）；
            #   无源音箱 → 仍走通用相邻级配对（管理器 / 功放 → SPK 口）。
            #   两者都必须用 fed（已有 SPEAKER 进线的 uid）过滤，
            #   否则无源音箱会被连两遍（菏泽 40 → 66 根 SPK 线）。
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
        if a == "ANT_COMBINE" or b == "ANT_COMBINE":
            # 合路器相邻对走专属规则：天线→合路器已在 0.8 连好；
            # 合路器→分配器在 b==ANT_DIST 分支处理。跳过通用配对避免重复。
            continue
        # ★ MIC_HOST（会议主机）走专用规则：只画 1 路 MIX→核心级，PHX 分区
        #   凤凰端出图不连。否则会被相邻阶段配对成「5 条 XLR 全连到首个核心级」。
        if a == "SOURCE" and any(d.category == "MIC_HOST"
                                for d in by_stage.get(a, [])):
            continue
        _generic_pair(project, by_stage.get(a, []), by_stage.get(b, []),
                      skip_out_uids=cascade_slaves if a == "MIXER" else None)

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
    """音源级接入首个核心级。

    级联调音台的从机也保留在目标列表里——它的 IN 口同样要接话筒
    （级联的意义就是扩展输入路数）；从机的 OUT 由 connect() 单独跳过。

    ★ MIC_HOST（会议主机）单独走 _connect_mic_host：只画 1 路 MIX → MIXER
    （无则 PROCESSOR），其它 out 端口（如分区凤凰端 PHX）出图不连。
    """
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
        devs = list(by_stage.get(src, []))
        if not devs:
            continue
        non_host = []
        for d in devs:
            if d.category == "MIC_HOST":
                _connect_mic_host(project, d, by_stage)
            else:
                non_host.append(d)
        if non_host:
            _generic_pair(project, non_host, tdevs)


def _connect_mic_host(project, host, by_stage):
    """会议主机只画 1 路 MIX → 核心级；PHX 分区凤凰端出图不连。

    阳哥规则（2026-08-31）：
      - CF6300 等会议主机一般只用 1 路 MIX（XLR out）送调音台；
      - 剩余 PHX 分区输出虽标 out，但「出图不用连接」；
      - 目标优先 MIXER，无则 PROCESSOR（小型系统用处理器代调音台）。
    """
    # 取 1 路：优先选 label 含 "MIX" 的 XLR out，否则第一个 XLR out
    out_port = None
    for p in host.ports:
        if p.role == "out" and p.signal == Signal.XLR and not p.air:
            if "MIX" in (p.label or "").upper():
                out_port = p
                break
    if out_port is None:
        for p in host.ports:
            if p.role == "out" and p.signal == Signal.XLR and not p.air:
                out_port = p
                break
    if out_port is None:
        return

    # 目标：MIXER 优先，无则 PROCESSOR（PROC_PRE / PROC_POST 都算）
    target_dev = None
    for cat in ("MIXER", "PROC_PRE", "PROC_POST"):
        ds = by_stage.get(cat, [])
        if ds:
            target_dev = ds[0]
            break
    if target_dev is None:
        return

    # 取目标第一个**空闲** XLR in：相邻级通用配对可能已占用前面的输入口
    # （如 1F 拓扑 GMN1208D 已占 QU-16 的 IN1~IN4），必须顺延到下一个空闲口，
    # 否则 _claim_input 拦截双源后会整体放弃连接、被孤立救援错接成 PHX→处理器。
    for p in target_dev.ports:
        if p.role == "in" and p.signal == Signal.XLR and not p.air:
            if _claim_input(project, Connection(
                    host.uid, out_port.id, target_dev.uid, p.id, Signal.XLR,
                    "primary", note="会议主机 MIX→核心级")):
                return


# ---- 无线天线链路（真分集 + 天线分配器级联） ----
# 默认级联预留出口数（仅当型号未显式声明 params.cascade_outs 时使用）
CASCADE_OUTS = 2


def _cascade_outs(dist):
    """取该分配器的级联预留出口数。

    天线分配器（ANT_DIST）一般均可级联：CASCADE_OUTS 默认 2，
    型号可在 params 显式覆盖（合路器 UM2000ASD = 0 不级联）：
      - IPS UM2000ATD = 2（官网：「级联端口能够以链式形式连接多套天线分配系统」）
      - AUDIX ADS48   = 2（阳哥 2026-09-01 确认：每 2 路输出给下一台输入，可级联）
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


def _wire_antennas_to_combiner(project, by_stage):
    """有天线合路器（ANT_COMBINE）时：天线(OUT) → 合路器(IN)。

    合路器把多副天线的 RF 信号合并成一路（阳哥 2026-09-01 确认，IPS UM2000ASD）。
    合并后的信号由 _antennas_to_first_dist 在「合路器(OUT) → 分配器(IN)」段接出。

    必须在相邻 stage 通用配对**之前**调用：否则 (ANTENNA, ANT_COMBINE) 这对会被
    _generic_pair 当成普通相邻对连一次（空 note），与这里的连线端口对重复，
    经 _dedup 去重后保留空 note 版本，正确的 note 反而被丢弃。
    """
    combiners = by_stage.get("ANT_COMBINE", [])
    if not combiners:
        return
    aouts = _rf_ports(by_stage.get("ANTENNA", []), "out")
    cins = _rf_ports(combiners, "in")
    for (ad, ap), (cd, cp) in zip(aouts, cins):
        project.connections.append(Connection(
            ad.uid, ap.id, cd.uid, cp.id, Signal.RF, "primary", note="天线→合路器"))


def _antennas_to_first_dist(project, by_stage):
    """合路器(OUT) → **首台**天线分配器(IN)；无合路器时 天线(OUT) → 首台分配器(IN)。

    - 有合路器（ANT_COMBINE）：合并后的信号（合路器 OUT）进首台分配器。
      天线→合路器 一段已由 _wire_antennas_to_combiner 在相邻配对前连好。
    - 无合路器：天线(OUT) → 首台分配器(IN)（原逻辑）。

    第 2 台起的分配器进口由上一台的级联出口供信号，不在此处理。
    """
    dists = by_stage.get("ANT_DIST", [])
    if not dists:
        return
    combiners = by_stage.get("ANT_COMBINE", [])
    if combiners:
        # 合路器(OUT) -> 首台分配器(IN)：合并后的信号进分配器
        couts = _rf_ports(combiners, "out")
        dins = _rf_ports([dists[0]], "in")
        for (cd, cp), (dd, dp) in zip(couts, dins):
            project.connections.append(Connection(
                cd.uid, cp.id, dd.uid, dp.id, Signal.RF, "primary", note="合路器→分配器"))
    else:
        # 无合路器：天线直接进首台分配器
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
    no_rf_port = set()   # 未建模 RF 天线口的接收机（不能硬凑口，只能跳过）

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
            if not rins:
                # 接收机未建模 RF 天线口（主库 ports_override 缺省或纯数字接口），
                # 无法接分配器——跳过而不是硬凑 1 口（否则 rins[k] 越界崩溃）。
                # 必须先 pop 再跳过，否则会卡死在 while 里反复处理同一台。
                pending.pop(0)
                no_rf_port.add(rx.uid)
                continue
            need = len(rins)
            if i + need > len(usable):
                # 本台剩余出口不够这台接收机：留在 pending 交给下一台分配器。
                # 关键：绝不能先 pop 再 break——否则这台接收机会从 pending 消失，
                # 既不连线、也不触发末尾「出口不足」告警，静默丢失（S1 修复）。
                break
            pending.pop(0)
            for k, (sd, sp) in enumerate(usable[i:i + need]):
                td, tp = rins[k]
                project.connections.append(Connection(
                    sd.uid, sp.id, td.uid, tp.id, Signal.RF, "primary",
                    note="天线分配"))
            i += need

    if no_rf_port:
        names = sorted({next((x.model for x in project.instances
                              if x.uid == u), u) for u in no_rf_port})
        project.meta.setdefault("wireless_warnings", []).append(
            f"{len(no_rf_port)} 台无线接收机未建模天线输入口，未接入分配器："
            f"{'、'.join(names)}（请在主库补 RF 天线口）")

    if pending:
        need = sum(len(_rf_ports([r], "in")) or 1 for r in pending)
        project.meta.setdefault("wireless_warnings", []).append(
            f"天线分配器出口不足：{len(pending)} 台无线接收机未分配到天线口"
            f"（尚缺 {need} 口，建议增加 UM2000ATD）")

    # 容量测算：给出「带这些接收机实际需要几台分配器」的提示
    wired_rxs = [r for r in rxs if _rf_ports([r], "in")]
    if wired_rxs:
        per_rx = max(len(_rf_ports([r], "in")) for r in wired_rxs)
        outs = max((len(_rf_ports([d], "out")) for d in dists), default=0)
        cas = _cascade_outs(dists[0]) if dists else CASCADE_OUTS
        req = required_dist_count(len(wired_rxs), per_rx, outs, cas)
        project.meta["wireless_plan"] = {
            "receivers": len(wired_rxs),
            "antennas_per_receiver": per_rx,
            "dists": len(dists),
            "dists_required": req,
            "outputs_per_dist": outs,
            "ok": len(dists) >= req,
        }
        if len(dists) < req:
            project.meta.setdefault("wireless_warnings", []).append(
                f"天线分配器数量不足：{len(wired_rxs)} 台接收机至少需要 {req} 台"
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


def _claim_input(project, conn):
    """占用目标输入口后追加连线；同一输入口被第二个信号源占用时跳过并告警。

    双源冲突（两个不同信号源接入同一设备输入口）在物理上不可行，且 ``_dedup``
    仅按完整 from/to 元组去重，抓不到「不同源 → 同一输入口」，所以这里用全局
    输入口占用表在落线源头拦截（覆盖 _generic_pair / _connect_mic_host / _failover）。
    """
    key = (conn.to_uid, conn.to_port)
    prev = project._used_in.get(key)
    if prev is not None:
        project.meta.setdefault("double_source_warnings", []).append(
            f"输入口双源冲突：{conn.to_uid}:{conn.to_port} 已接 {prev}，"
            f"{conn.from_uid}:{conn.from_port}({conn.signal}) 接入被跳过")
        return False
    project._used_in[key] = f"{conn.from_uid}:{conn.from_port}({conn.signal})"
    project.connections.append(conn)
    return True


def _generic_pair(project, a_devs, b_devs, skip_out_uids=None):
    """通用相邻级配对：A 的出口按信号类型依次连 B 的进口。

    ``skip_out_uids``：跳过这些设备的**出口**（级联从机的音频已随 LINK
    汇总到主机，再出线就成了同一路信号画两根）。
    """
    outs, ins = [], []
    skip = skip_out_uids or set()
    for d in a_devs:
        if d.uid in skip:
            continue
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
                # ★ R19 接语义：以 device_kb.yaml 的上下游为权威闸门。
                #   这对类别没有上下游关系就跳过，避免画出语义错乱的线
                #   （如处理器→无线话筒这类反向/越界连接）。
                ok, reason = category_kb.is_valid_link(d.category, td.category)
                if not ok:
                    project.meta.setdefault("kb_warnings", []).append(
                        f"跳过语义越界连线：{d.name}({d.category})→{td.name}({td.category})：{reason}")
                    continue
                role = _role(d, td)
                _claim_input(project, Connection(
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
            for ci, suids, mode, total, ok, note, power_ok in res:
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
                        # A 批前置改造：计算层直出 (level, code, msg) 三元组，
                        # 不再把定级推给校验层用中文子串嗅探（findings/37 §4.1）。
                        project.meta.setdefault("amp_warnings", []).append(
                            ("ERROR", "IMPEDANCE",
                             f"功放{amp.name}通道{ci+1}: {note}"))
                    if not power_ok:
                        # AMP_ 功率裕量不足：advisory WARN，不阻断出图。
                        # 此前只塞进 note 字符串、随阻抗 ERROR 文案一起被吞掉，
                        # 正常阻抗（ok=True）下则彻底无从报出 —— 现独立成码。
                        sp_power = sum(s.params.get("power_w", 200) for s in grp)
                        amp_power = amp.electrical.get("power_w_per_ch", 1000)
                        project.meta.setdefault("amp_warnings", []).append(
                            ("WARN", "AMP_UNDERPOWERED",
                             f"功放{amp.name}通道{ci+1}: 功率裕量不足"
                             f"(功放{amp_power}W < 扬声器总额定{sp_power}W×1.2)"))
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
        # 有源音箱只接**空闲**出口，否则同一端口被复用：图上看着线数没变，
        # 实际是两根线压在同一个口上（阳哥 2026-08-30：「调音台并没有增加输出的线」）。
        used_out = {(c.from_uid, c.from_port) for c in project.connections
                    if c.signal in (Signal.XLR, Signal.AES)}
        # 模拟/数字音频出口（XLR / AES），只接空闲口。Dante 一律经交换机承载
        # （_dante_pass），所以有源音箱同时拿到「模拟 + Dante」两条独立链路，互不冲突
        # （阳哥 2026-08-30：BF12 既要模拟又要 Dante）。
        pins = []
        for d in prev_devs:
            for p in d.ports:
                if (p.role == "out" and p.signal in (Signal.XLR, Signal.AES)
                        and not p.air and (d.uid, p.id) not in used_out):
                    pins.append((d, p))
        # 每台有源音箱各接一路模拟/数字音频（与落点端口类型一致），Dante 交给 _dante_pass。
        for s in active:
            ap = next((p for p in s.ports
                       if p.role == "in" and p.signal in (Signal.XLR, Signal.AES)
                       and not p.air), None)
            if ap is None or not pins:
                continue
            # 优先同信号类型的出口（XLR→XLR、AES→AES）
            k = next((i for i, (_d, _p) in enumerate(pins)
                      if _p.signal == ap.signal), 0)
            d, p = pins.pop(k)
            project.connections.append(
                Connection(d.uid, p.id, s.uid, ap.id, ap.signal, _role(d, s)))


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
            ok, reason = category_kb.is_valid_link(d.category, td.category)
            if not ok:
                project.meta.setdefault("kb_warnings", []).append(
                    f"跳过语义越界连线(救援)：{d.name}({d.category})→{td.name}({td.category})：{reason}")
                continue
            role = _role(d, td)
            project.connections.append(Connection(
                d.uid, p.id, td.uid, tp.id, p.signal, role,
                note="音源直入" if td.category == "MIXER" else ""))
            free.pop(k)
            break


def _conf_signal():
    """会议专用线（六芯主缆 / T 型线）。取不到 CONF 时退回 XLR。"""
    return getattr(Signal, "CONF", Signal.XLR)


def _conf_buses(host):
    """会议主机上用于接手拉手链的六芯主缆进口（CH）。

    排除 BOX——那是留给天线盒的专用口，不能被会议单元链占用
    （曾导致 10 只 CF6320 被拆成 5 条短链，其中一条还错接在 BOX 上）。
    """
    sig = _conf_signal()
    ports = [p for p in host.ports
             if p.role == "in" and p.signal == sig and not p.air]
    named = [p for p in ports if p.id.rsplit(":", 1)[-1].startswith("CH")]
    return named or ports


def _conf_box_port(host):
    """会议主机上接天线盒的专用口（BOX）。"""
    sig = _conf_signal()
    return next((p for p in host.ports
                 if p.role == "in" and p.signal == sig
                 and p.id.rsplit(":", 1)[-1].startswith("BOX")), None)


def _mixer_cascade(project):
    """可级联调音台（如 IPS AM860 自动混音器）用 LINK 口串成扩展总线。

    主库约定：MIXER 的 ``params.cascade`` > 0 时模板会生成一对 LINK_IN /
    LINK_OUT。多台**同品牌同型号**的可级联调音台按 BOM 顺序串成一条链::

        第1台(主机) LINK_OUT → 第2台 LINK_IN → 第3台 LINK_IN …

    从机的音频已随 LINK 汇总到主机，因此从机的 OUT **不再参与后级连线**
    （否则同一路信号会被画成两根线）。返回从机 uid 集合供 connect() 过滤。
    """
    groups = defaultdict(list)
    for i in project.instances:
        if i.stage != "MIXER":
            continue
        pr = getattr(i, "params", None) or {}
        try:
            if int(pr.get("cascade", 0) or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        if not any(p.role == "out" and p.signal == Signal.LINK for p in i.ports):
            continue
        groups[(i.brand or "", i.model or "")].append(i)

    slaves = set()
    for (_brand, _model), devs in groups.items():
        if len(devs) < 2:
            continue
        prev = None
        for d in sorted(devs, key=lambda x: x.uid):
            if prev is not None:
                p_out = next((p for p in prev.ports
                              if p.role == "out" and p.signal == Signal.LINK), None)
                p_in = next((p for p in d.ports
                             if p.role == "in" and p.signal == Signal.LINK), None)
                if p_out and p_in:
                    project.connections.append(Connection(
                        prev.uid, p_out.id, d.uid, p_in.id,
                        Signal.LINK, "primary", note="级联扩展总线"))
                    slaves.add(d.uid)
            prev = d
    return slaves


def _conference_link(project, by_stage):
    """会讨手拉手：有线会议单元用 T 型线串成链后，由六芯主缆接入会议主机。

    官方（ezpro CF63 系列）：「会议主机与有线会议单元、天线盒均采用专用六芯
    主缆连接，传输距离达 100 米」「有线系统单元间采用 T 型线连接」「支持有线
    单元环形手拉手连接，某台单元故障不影响整套系统工作」。

    ★ 每只单元就是**一进一出**：上一只的 DIN_OUT → 本只的 DIN_IN；
      链上最后一只（最靠近主机）的 DIN_OUT → 主机的一个 CH 六芯主缆口。

    主库侧约定：会议单元 ``params.host`` 指向主机型号（如 CF6300），
    带一对 DIN_IN / DIN_OUT。链数按单链容量 ``params.conf_chain_max``
    （默认 20）与主机 CH 口数取小，避免把 10 只单元拆成 5 条两单元的碎链。

    返回已接线的单元 uid 集合——这些单元不应再参与 SOURCE 的星型配对。
    """
    sig = _conf_signal()
    hosts = [i for i in project.instances if i.category == "MIC_HOST"]
    if not hosts:
        return set()

    # 单元按 params.host 归属到对应主机；没写 host 的挂到第一台主机
    buckets = defaultdict(list)
    for i in project.instances:
        pr = getattr(i, "params", None) or {}
        # conf_box（无线会讨天线盒）走 _conference_box_link 的 BOX 口，
        # 不能混进手拉手链——否则它会既连 CH 口又连 BOX 口，主机上多出一条线。
        if not pr.get("host") or pr.get("conf_wireless") or pr.get("conf_box"):
            continue
        if not any(p.role == "in" and p.signal == sig for p in i.ports):
            continue
        buckets[pr.get("host")].append(i)

    wired = set()
    for host in hosts:
        units = buckets.get(host.model) or buckets.get(None) or []
        if not units:
            continue
        buses = _conf_buses(host)
        if not buses:
            continue
        cap = max(1, int((getattr(host, "params", None) or {})
                         .get("conf_chain_max", 20) or 20))
        # 链数：够串就少分链，链条数不超过主机总线口数
        n_chain = min(len(buses), max(1, -(-len(units) // cap)))
        chains = [[] for _ in range(n_chain)]
        for idx, u in enumerate(units):
            chains[idx % n_chain].append(u)

        for k, ch in enumerate(chains):
            if not ch:
                continue
            prev = None
            for u in ch:
                u_in = next((p for p in u.ports
                             if p.role == "in" and p.signal == sig), None)
                if prev is not None and u_in is not None:
                    p_out = next((p for p in prev.ports
                                  if p.role == "out" and p.signal == sig), None)
                    if p_out:
                        project.connections.append(Connection(
                            prev.uid, p_out.id, u.uid, u_in.id, sig,
                            "primary", note="手拉手 T 型线"))
                        wired.add(prev.uid)
                prev = u
            # 链上最靠近主机的一只 → 主机六芯主缆口
            last_out = next((p for p in prev.ports
                             if p.role == "out" and p.signal == sig), None)
            if last_out:
                project.connections.append(Connection(
                    prev.uid, last_out.id, host.uid, buses[k].id, sig,
                    "primary", note="六芯主缆→主机"))
                wired.add(prev.uid)
    return wired


def _conference_box_link(project, by_stage, conf_units):
    """会讨天线盒链路：天线盒 → 主机 BOX 口（六芯主缆）；无线单元 → 天线盒（RF）。

    CF6300WB 是「无线会讨天线盒」：无线会议单元（CF6360/CF6350）的 UHF 信号
    由它接收，再通过专用六芯主缆送回 CF6300 主机的 BOX 口。
    官方：「会议主机与有线会议单元、天线盒均采用专用六芯主缆连接」。
    """
    sig = _conf_signal()
    hosts = [i for i in project.instances if i.category == "MIC_HOST"]
    if not hosts:
        return set()
    boxes = [i for i in project.instances
             if (getattr(i, "params", None) or {}).get("conf_box")]
    if not boxes:
        return set()

    linked = set()
    for bi, box in enumerate(boxes):
        host = next((h for h in hosts if h.model ==
                     ((getattr(box, "params", None) or {}).get("host") or hosts[0].model)),
                    hosts[0])
        bport = _conf_box_port(host)
        # 主机只有一个 BOX 口时，第二台起的天线盒串到前一台的 CONF 出口
        if bport is None or any(c.to_port == bport.id and c.to_uid == host.uid
                                for c in project.connections):
            prev = boxes[bi - 1] if bi else None
            hp = None
            if prev is not None:
                hp = next((p for p in prev.ports
                           if p.role == "in" and p.signal == sig
                           and not any(c.to_uid == prev.uid and c.to_port == p.id
                                       for c in project.connections)), None)
            if hp is None:
                continue
            op = next((p for p in box.ports
                       if p.role == "out" and p.signal == sig), None)
            if op is None:
                continue
            project.connections.append(Connection(
                box.uid, op.id, prev.uid, hp.id, sig, "primary",
                note="天线盒级联"))
            linked.add(box.uid)
            continue
        op = next((p for p in box.ports
                   if p.role == "out" and p.signal == sig), None)
        if op is None:
            continue
        project.connections.append(Connection(
            box.uid, op.id, host.uid, bport.id, sig, "primary",
            note="六芯主缆→主机"))
        linked.add(box.uid)

    # ★ 无线话筒/单元不画物理线（阳哥 2026-09-02 定）：RF 为空中接口，
    #   话筒经无线打到天线盒、天线盒再经无线连接收端，全程无线，图上线。
    #   此前这里曾画「单元 RF → 天线盒 RF」的线，属方向/信号都错的误连，已删。
    return linked


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
        # ★ 有源音箱允许「模拟 + Dante」同时接入（阳哥 2026-08-30：BF12 两者
        #   都要，互不冲突）。模拟由 _handle_speakers 从空闲出口接，Dante 由本
        #   函数经交换机接，是两条独立链路，故这里不做 SPEAKER 排除。
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
            # 链路冗余（LINK_BACKUP）的冗余在网络层：主备各走一台交换机，
            # 设备之间**不画** failover 直连线——画了就等于把主备串成一条链，
            # 备机反而失去了独立链路的意义。
            if not redundancy_scope(a.redundancy or b.redundancy)["failover_link"]:
                continue
            ap = _first_out(a, Signal.XLR) or _first_out(a, Signal.AES)
            bp = _first_in(b, Signal.XLR) or _first_in(b, Signal.AES)
            if ap and bp:
                _claim_input(project, Connection(
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
