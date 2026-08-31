"""会议主机 CF6300 与 IO 设备（接口箱）的链路规则（阳哥规则 2026-08-31）。

两条互相关联的修正：

  1. CF6300 等会议主机的输出（MIX/PHX）画法：
       · 只画 1 条 MIX → 核心级（MIXER 优先，无则 PROCESSOR）；
       · 剩余 PHX 分区凤凰端**出图不连**（备路）。
       · 此前被 chain 相邻阶段配对成「4×PHX + 1×MIX 全连处理器」是错的。

  2. IO 设备（DIO 4x4 / Rio3224 / RY16-AE 等）的角色：
       · 与调音台/处理器平级（侧层设备），不进入主链路串联；
       · 仅通过 DANTE 网络经交换机互通（DANTE 端口由 _dante_pass 处理）；
       · 不应画「MIXER → IO IN」或「IO OUT → AMP」这种 XLR 串联线。
       · 此前 chain.append("IO") 把它放在 PROC_POST 之后，被画成下一级。

回归防线（守住即可，不必新增功能）：
  · 会议主机的 MIX 必须连 MIXER（不是 PROCESSOR）——除非系统无调音台。
  · IO 设备的 XLR 端口在出图里**不应出现在连线列表**。
"""
from __future__ import annotations

import pytest

from avcad.core.build import build_project
from avcad.model.schema import Signal


def _cf6300():
    """会议主机 CF6300：4×CH + 4×PHX + 1×MIX + 1×BOX（主库 ports_override 形态）。"""
    return {"brand": "IPS", "model": "CF6300", "name": "有线无线融合会议主机",
            "quantity": 1, "category": "MIC_HOST"}


def _qu16():
    return {"brand": "ALLEN&HEATH", "model": "QU-16", "name": "数字调音台",
            "quantity": 1, "category": "MIXER",
            "params": {"inputs": 16, "outputs": 8}}


def _gmn1208d():
    """前置换机 GMN1208D（自动混音）：8 路 IN。"""
    return {"brand": "IPS", "model": "GMN1208D", "name": "前置换机",
            "quantity": 1, "category": "PROCESSOR",
            "params": {"inputs": 8, "outputs": 4}}


def _dio4x4():
    return {"brand": "IPS", "model": "DIO 4x4", "name": "Dante接口盒",
            "quantity": 1, "category": "IO",
            "params": {"inputs": 4, "outputs": 4}}


def _switch(n=1):
    return [{"brand": "ezpro", "model": "千兆交换机",
             "name": "Dante 交换机", "quantity": n}]


def _connections_from(p, uid):
    return [c for c in p.connections
            if c.from_uid == uid or c.to_uid == uid]


def _by_uid(p):
    return {i.uid: i for i in p.instances + p.switches}


def _lines(p, uid):
    uid_map = _by_uid(p)
    out = []
    for c in _connections_from(p, uid):
        a = uid_map[c.from_uid]; b = uid_map[c.to_uid]
        out.append((c.signal.value, a.category, a.model, b.category, b.model))
    return out


# ─────────────────────────────────────────────────────────────────────
# 修正 1：CF6300 输出只画 1 条 MIX → 核心级
# ─────────────────────────────────────────────────────────────────────


def test_cf6300_outputs_only_draw_mix_to_mixer_not_processor():
    """1F 场景：CF6300 + QU-16 + GMN1208D。
    CF6300 应只画 1 条 MIX → QU-16；不应连到前置处理器 GMN1208D；
    4×PHX 分区凤凰端应**出图不连**。
    """
    p = build_project([_cf6300(), _qu16(), _gmn1208d()] + _switch(),
                      name="1F会议室")
    uid_map = _by_uid(p)
    host = next(i for i in p.instances if i.category == "MIC_HOST")

    # 1) CF6300 出去的 XLR 连线必须**只有 1 条**（MIX → MIXER）
    xlr_outs = [c for c in _connections_from(p, host.uid)
                if c.signal == Signal.XLR and c.from_uid == host.uid]
    assert len(xlr_outs) == 1, (
        f"CF6300 出去的 XLR 应只 1 条（MIX→MIXER），实际 {len(xlr_outs)} 条："
        f"{[(c.from_port, c.to_uid, c.to_port) for c in xlr_outs]}"
    )

    # 2) 这 1 条必须连到调音台（不是处理器）
    only = xlr_outs[0]
    target = uid_map[only.to_uid]
    assert target.category == "MIXER", (
        f"CF6300 的 MIX 应连调音台（不是 {target.category}）")

    # 3) 这 1 条必须是 MIX 端口出去的，不是 PHX1-4
    out_port = next(pp for pp in host.ports if pp.id == only.from_port)
    assert "MIX" in (out_port.label or "").upper(), (
        f"出去的应是 MIX 端口，实际是 {out_port.label}")

    # 4) PHX1-4 分区凤凰端不应有任何连线（备路出图不连）
    phx_ports = [pp for pp in host.ports
                 if pp.role == "out" and (pp.label or "").upper().startswith("PHX")]
    for pp in phx_ports:
        leaks = [c for c in p.connections if c.from_port == pp.id]
        assert not leaks, f"PHX 端口 {pp.label} 出图应不连，但画了 {len(leaks)} 条"


def test_cf6300_falls_back_to_processor_when_no_mixer():
    """无调音台场景：CF6300 + GMN1208D。MIX 应退而连到处理器（小型系统）。"""
    p = build_project([_cf6300(), _gmn1208d()] + _switch(),
                      name="无调音台")
    host = next(i for i in p.instances if i.category == "MIC_HOST")
    xlr_outs = [c for c in _connections_from(p, host.uid)
                if c.signal == Signal.XLR and c.from_uid == host.uid]
    assert len(xlr_outs) == 1
    target = _by_uid(p)[xlr_outs[0].to_uid]
    assert target.category in ("PROCESSOR", "MIXER")


def test_cf6300_no_link_when_no_core():
    """极端场景：CF6300 单独存在（无调音台/处理器）。不应画 XLR 串联。"""
    p = build_project([_cf6300()] + _switch(), name="裸主机")
    host = next(i for i in p.instances if i.category == "MIC_HOST")
    xlr_outs = [c for c in p.connections
                if c.from_uid == host.uid and c.signal == Signal.XLR]
    assert not xlr_outs, (
        f"无核心级时 CF6300 不应连 XLR，实际画了 {len(xlr_outs)} 条")


# ─────────────────────────────────────────────────────────────────────
# 修正 2：IO 设备平级化（不画 XLR 串联）
# ─────────────────────────────────────────────────────────────────────


def test_io_box_has_no_xlr_serial_link():
    """4F 场景：QU-16 + DIO 4x4 + 调音台/功放路径。
    DIO 4x4 的 XLR IN/OUT 端口不应被画成「调音台→IO」或「IO→下级」串联。
    """
    # 4F 会议室的实际拓扑（DIO 4x4 是话筒与远端功放之间的接口箱）
    p = build_project([
        _qu16(),
        _dio4x4(),
        {"brand": "FIRM", "model": "FIRM4×8", "name": "音响管理器",
         "quantity": 1, "category": "SPEAKER_MGR",
         "params": {"inputs": 4, "outputs": 8}},
    ] + _switch(), name="4F会议室")
    io = next(i for i in p.instances if i.category == "IO")
    io_lines = _lines(p, io.uid)
    xlr_lines = [ln for ln in io_lines if ln[0] == "XLR"]
    assert not xlr_lines, (
        f"IO 设备不应有 XLR 串联线（与调音台平级，仅 DANTE 互通），"
        f"实际画了 {len(xlr_lines)} 条：{xlr_lines}")

    # 但 DANTE 经交换机的那条必须保留
    dante_lines = [ln for ln in io_lines if ln[0] == "DANTE"]
    assert dante_lines, "IO 设备的 DANTE 经交换机互通是必须的，不能丢"


def test_io_device_is_side_stage_not_in_main_chain():
    """IO 设备的 stage 必须是 SIDE（侧层设备），不进入主链路。
    验证 STAGE_LABELS["SIDE"] 已被 chain 模块识别。
    """
    from avcad.topology.chain import build_chain, STAGE_LABELS, assign_stages
    assert "SIDE" in STAGE_LABELS, "SIDE stage 标签必须存在"
    p = build_project([_dio4x4(), _qu16()] + _switch(), name="T")
    chain = build_chain(p.instances)
    assert "IO" not in chain, (
        f"IO 不应进入主 chain（与调音台平级），实际 chain={chain}")
    assign_stages(p.instances, chain)
    io = next(i for i in p.instances if i.category == "IO")
    assert io.stage == "SIDE", f"IO 设备 stage 应为 SIDE，实际 {io.stage}"


# ─────────────────────────────────────────────────────────────────────
# 回归防线：CF6300/IO 改动不破坏其他连线
# ─────────────────────────────────────────────────────────────────────


def test_no_cf6300_still_links_normally_to_mixer():
    """无 CF6300 时，SOURCE→MIXER 的连线行为不变（chain 相邻阶段仍工作）。
    用主库里的有线话筒 CF6320（会议单元 + CONF 链不在此验证范围）。"""
    p = build_project([
        # 直接用 SOURCE 默认模板的 XLR 话筒（brand/model 不在主库 → 走默认）
        {"brand": "TEST", "model": "XLR-MIC", "name": "测试话筒",
         "quantity": 3, "category": "SOURCE"},
        _qu16(),
    ] + _switch(), name="普通XLR话筒")
    # 3 支话筒应各连 1 条 XLR 到 QU-16
    src_xlr = [c for c in p.connections if c.signal == Signal.XLR]
    assert len(src_xlr) >= 3, (
        f"普通 XLR 话筒应正常连调音台，实际 XLR 连线数 {len(src_xlr)}")
