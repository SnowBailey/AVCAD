"""功放↔扬声器匹配：优先 8Ω 独立通道（每通道 1 只），仅当扬声器数 > 通道数时
才把剩余扬声器并联到已有通道（轮流追加以均衡阻抗与负载）；越限回退串联，并做功率校验。
公式：n 只同阻 R 并联总阻 R/n；串联总阻 nR。需 R_total >= 功放最低负载。"""
from __future__ import annotations


def match_speakers_to_amp(amp, speakers: list) -> list:
    """返回 [(channel_idx, [speaker_uids], mode, total_ohm, ok, note), ...]"""
    min_load = amp.electrical.get("min_load_ohm", 4)
    amp_power = amp.electrical.get("power_w_per_ch", 1000)
    ch = int(amp.params.get("channels", len([p for p in amp.ports if p.signal.name == "SPEAKER"])))
    ch = max(1, ch)
    sp = list(speakers)
    n = len(sp)

    # 1) 优先 8Ω 独立通道：每个通道先分配 1 只扬声器
    groups = [[sp[i]] for i in range(min(ch, n))]
    leftover = sp[min(ch, n):]
    # 2) 通道数不足：剩余扬声器并联到已有通道（轮流追加，均衡阻抗与负载）
    ci = 0
    for s in leftover:
        groups[ci % len(groups)].append(s)
        ci += 1
    # 补齐空通道
    while len(groups) < ch:
        groups.append([])

    result = []
    for ci, grp in enumerate(groups):
        if not grp:
            result.append((ci, [], "none", 0, True, "空通道", True))
            continue

        def _ohm(s):
            v = s.params.get("impedance_ohm", 8)
            if isinstance(v, (list, tuple)):
                return v[0] if v else 8
            return v or 8

        impedances = [_ohm(s) for s in grp]
        equal = len(set(impedances)) == 1
        R = impedances[0]
        m = len(grp)
        if m == 1:
            # 单只：8Ω 独立通道，最优先配置
            mode, total, ok = "independent", R, True
            note = f"{R}Ω 独立通道"
        elif equal:
            r_par = R / m
            r_ser = R * m
            if r_par >= min_load:
                mode, total = "parallel", round(r_par, 2)
                ok = True
                note = f"{m}×{R}Ω 并联={total}Ω ≥ {min_load}Ω"
            elif r_ser <= min_load * 4:
                mode, total = "series", round(r_ser, 2)
                ok = total <= min_load * 8
                note = f"{m}×{R}Ω 并联越限→串联={total}Ω"
            else:
                mode, total = "parallel", round(r_par, 2)
                ok = False
                note = f"阻抗越限：并联{round(r_par,2)}Ω / 串联{round(r_ser,2)}Ω 均不满足≥{min_load}Ω"
        else:
            # 不同阻：并联 1/Z=Σ1/Zi，串联直接求和
            r_par = 1.0 / sum(1.0 / z for z in impedances)
            r_ser = sum(impedances)
            if r_par >= min_load:
                mode, total = "parallel", round(r_par, 2)
                ok = True
                note = f"混合阻并联={total}Ω ≥ {min_load}Ω"
            elif r_ser <= min_load * 4:
                mode, total = "series", round(r_ser, 2)
                ok = total <= min_load * 8
                note = f"混合阻并联越限→串联={total}Ω"
            else:
                mode, total = "parallel", round(r_par, 2)
                ok = False
                note = f"阻抗越限：并联{round(r_par,2)}Ω / 串联{round(r_ser,2)}Ω 均不满足≥{min_load}Ω"
        # 功率裕量校验：功放功率 ≥ 扬声器总额定×1.2 为设计建议（advisory），
        # 仅作 WARN 提示，不阻断出图；阻抗越限（ok=False）才为 ERROR。
        # ★ 功率裕量不足不再塞进 note（note 只描述阻抗），改为独立标志 power_ok，
        #   由 router 单独发 AMP_POWER_MARGIN 告警，避免被阻抗 ERROR 文案吞掉、
        #   或在阻抗正常（ok=True）时彻底无从报出（此前是静默漏报）。
        sp_power = sum(s.params.get("power_w", 200) for s in grp)
        power_ok = amp_power >= sp_power * 1.2
        result.append((ci, [s.uid for s in grp], mode, total, ok, note, power_ok))
    return result
