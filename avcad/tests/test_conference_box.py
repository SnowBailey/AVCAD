"""会讨天线盒链路（2026-08-31 补验：此前从未被真实清单覆盖）。

拓扑（ezpro CF63 系列，主机 CF6300）：

    无线会议单元 CF6350/CF6360 ──RF(air)──▶ 无线会讨天线盒 CF6300WB
                                                    │ 六芯主缆（CONF）
                                                    ▼
                                          会议主机 CF6300 的 BOX 口

关键设计：
  · 无线单元与天线盒之间**没有物理接头**——主库用 ``air: True`` 的 RF 口表达：
    渲染只画圆点不画接头标签，且被 ``_generic_pair`` / ``_rf_ports`` 过滤，
    不会退化成「单元 XLR OUT → 天线盒 RF」这类方向/信号都错的连线。
  · 天线盒本身不是手拉手单元：它走 BOX 口，不能混进 ``_conference_link``
    的 CH 口链路，否则主机上会同时出现 CH 与 BOX 两条线（历史缺陷）。

回归防线：
  · CF6804/CF6808 是**独立无线会议系统接收机**（WIRELESS_RX），不是 CF6300
    的配套会讨单元——曾误标 ``conf_wireless`` 被拉去连天线盒。
  · 未建模 RF 天线口的接收机会让天线分配逻辑索引越界（历史崩溃点）。
"""
from __future__ import annotations

import collections

from avcad.core.build import build_project
from avcad.model.schema import Signal


def _ips(model, name, n=1):
    return {"brand": "IPS", "model": model, "name": name, "quantity": n}


def _mixer():
    return {"brand": "ALLEN&HEATH", "model": "QU-16", "name": "数字调音台",
            "quantity": 1, "category": "MIXER",
            "params": {"inputs": 16, "outputs": 8}}


def _uids_by_model(p, model):
    return {i.uid for i in p.instances if i.model == model}


def test_wireless_units_not_wired_to_box():
    """阳哥 2026-09-02 定：无线话筒不接线（RF 为空中口），单元与天线盒之间无物理线。

    回归：此前曾画「单元 RF → 天线盒 RF」的线，方向/信号都错，已删。
    """
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒"),
                       _ips("CF6350", "无线数字会议单元", 4)], name="T")
    box_uids = _uids_by_model(p, "CF6300WB")
    unit_uids = _uids_by_model(p, "CF6350")
    assert box_uids and len(unit_uids) == 4

    # 无线话筒不接线：单元与天线盒之间不应有任何连线
    rf = [c for c in p.connections
          if c.from_uid in unit_uids and c.to_uid in box_uids]
    assert len(rf) == 0, f"无线话筒不连线，不应有单元→天线盒连线，实为 {len(rf)}"
    # 天线盒端口已精简为只剩 HOST+CASCADE（阳哥：只有 HOST 即可），无 RF 空中口
    box = next(i for i in p.instances if i.model == "CF6300WB")
    assert {q.signal for q in box.ports} == {Signal.CONF}, \
        f"天线盒端口应只有 CONF（HOST+CASCADE），实为 {[q.signal for q in box.ports]}"


def test_box_uses_host_box_port_only():
    """天线盒只能接主机 BOX 口 1 条线——不得再混进手拉手 CH 链路。"""
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒"),
                       _ips("CF6350", "无线数字会议单元", 4),
                       _ips("CF6320", "会议单元", 3)], name="T")
    host = next(i for i in p.instances if i.model == "CF6300")
    box = next(i for i in p.instances if i.model == "CF6300WB")

    to_host = [c for c in p.connections if c.to_uid == host.uid]
    from_box = [c for c in to_host if c.from_uid == box.uid]
    assert len(from_box) == 1, f"天线盒→主机应 1 条，实为 {len(from_box)}"

    box_ports = {q.id for q in host.ports if q.id.endswith(":BOX_1")}
    assert box_ports, "主机未建模 BOX 口"
    assert from_box[0].to_port in box_ports, \
        f"天线盒接到了 {from_box[0].to_port}，应走 BOX 口"

    # 有线单元各自串链入 CH 口，两条链路互不串台
    ch_ports = {q.id for q in host.ports if ":CH_" in q.id}
    wired_to_host = [c for c in to_host if c.from_uid != box.uid]
    assert wired_to_host, "有线手拉手链没有汇入主机"
    for c in wired_to_host:
        assert c.to_port in ch_ports, f"有线链接到了非 CH 口：{c.to_port}"
    assert not ({c.to_port for c in from_box} &
                {c.to_port for c in wired_to_host}), "BOX 口与 CH 口被混用"


def test_multiple_boxes_cascade_and_share_units():
    """两台天线盒：第 2 台级联到第 1 台，无线单元在两台间分摊。"""
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒", 2),
                       _ips("CF6350", "无线数字会议单元", 6)], name="T")
    box_uids = sorted(_uids_by_model(p, "CF6300WB"))
    assert len(box_uids) == 2

    boxes = [i for i in p.instances if i.model == "CF6300WB"]
    casc = [c for c in p.connections
            if c.from_uid in box_uids and c.to_uid in box_uids]
    assert len(casc) == 1, f"两台天线盒应有 1 条级联线，实为 {len(casc)}"

    unit_uids = _uids_by_model(p, "CF6350")
    # 阳哥 2026-09-02 定：无线话筒不接线，单元与天线盒之间无物理线
    rf = [c for c in p.connections
          if c.from_uid in unit_uids and c.to_uid in box_uids]
    assert len(rf) == 0, f"无线话筒不连线，不应有单元→天线盒连线，实为 {len(rf)}"
    # 级联口是 CONF（六芯主缆），不是 RF
    assert casc[0].signal == Signal.CONF


def test_standalone_wireless_system_not_linked_to_box():
    """回归：CF6804/CF6808 是独立无线会议系统，不得被拉去连会讨天线盒。

    它们曾误标 ``conf_wireless``，被画成「CF6804 XLR MIX OUT → 天线盒 RF」，
    信号类型与方向全错。
    """
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒"),
                       _ips("CF6804", "四通道无线会议系统"),
                       _ips("CF6808", "八通道无线会议系统")], name="T")
    box_uids = _uids_by_model(p, "CF6300WB")
    sys_uids = _uids_by_model(p, "CF6804") | _uids_by_model(p, "CF6808")
    assert sys_uids, "两套独立无线会议系统未建模"

    bad = [c for c in p.connections
           if c.from_uid in sys_uids and c.to_uid in box_uids]
    assert not bad, f"独立无线会议系统被连到会讨天线盒：{bad}"
    # 它们也绝不该发出 RF 线（是接收机，不是发射端）
    assert not [c for c in p.connections
                if c.from_uid in sys_uids and c.signal == Signal.RF]


def test_receiver_without_rf_port_does_not_crash():
    """回归：接收机未建模 RF 天线口时不得越界崩溃，只能跳过并告警。"""
    p = build_project([_ips("CF6804", "四通道无线会议系统"),
                       _ips("UM2002", "无线话筒"),
                       _ips("UM2000AP", "天线"),
                       _mixer()], name="T")
    warns = p.meta.get("wireless_warnings") or []
    assert any("未建模天线输入口" in w for w in warns), \
        f"应给出未建模天线口的告警，实为 {warns}"
    # UM2002 仍应正常拿到 4 个天线口
    rx = next(i for i in p.instances if i.model == "UM2002")
    got = [c for c in p.connections
           if c.to_uid == rx.uid and c.signal == Signal.RF]
    assert len(got) == 4, f"UM2002 应分到 4 个天线口，实为 {len(got)}"


def test_wireless_units_do_not_pair_star_to_mixer():
    """已接入会讨链路的无线单元不得再直连调音台。"""
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒"),
                       _ips("CF6350", "无线数字会议单元", 4),
                       _mixer()], name="T")
    mixer_uid = next(i.uid for i in p.instances if i.category == "MIXER")
    unit_uids = _uids_by_model(p, "CF6350")
    bad = [c for c in p.connections
           if c.from_uid in unit_uids and c.to_uid == mixer_uid]
    assert not bad, "无线会议单元直连调音台了，应统一经主机汇入"


def test_conf_box_does_not_trigger_auto_distributor():
    """回归：会讨天线盒自带 UHF 接收，不得触发自动补天线分配器。

    曾按「有 ANTENNA 就补分配器」处理，结果图上凭空多出一台空型号的
    孤立分配器（2026-08-31 样例图暴露）。
    """
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒", 2),
                       _ips("CF6350", "无线数字会议单元", 6)], name="T")
    dists = [i for i in p.instances if i.category == "ANT_DIST"]
    assert not dists, \
        f"会讨场景不应出现天线分配器，实为 {[i.model for i in dists]}"

    # 会讨天线盒本身就该是 ANTENNA，且不带分配器时也不能成为孤立节点
    linked = {c.from_uid for c in p.connections} | {
        c.to_uid for c in p.connections}
    boxes = [i for i in p.instances if i.model == "CF6300WB"]
    assert boxes and all(b.uid in linked for b in boxes), \
        "天线盒成了孤立节点"


def test_external_antenna_still_triggers_auto_distributor():
    """反向回归：真外接天线 + 接收机仍应自动补分配器。"""
    p = build_project([_ips("UM2002", "无线话筒"),
                       _ips("UM2000AP", "天线"),
                       _mixer()], name="T")
    dists = [i for i in p.instances if i.category == "ANT_DIST"]
    assert dists, "外接天线 + 接收机应自动补一台分配器"
    rx = next(i for i in p.instances if i.model == "UM2002")
    got = [c for c in p.connections
           if c.to_uid == rx.uid and c.signal == Signal.RF]
    assert len(got) == 4, f"UM2002 应分到 4 个天线口，实为 {len(got)}"


def test_air_ports_render_as_dots_only():
    """air 口只画圆点：不出接头标签，也不参与有线配对。"""
    p = build_project([_ips("CF6300", "有线无线融合会议主机"),
                       _ips("CF6300WB", "无线会讨天线盒"),
                       _ips("CF6350", "无线数字会议单元", 2)], name="T")
    airs = [(i, q) for i in p.instances for q in i.ports if q.air]
    assert airs, "本场景应存在 air 空中口"
    for inst, q in airs:
        assert q.signal == Signal.RF, f"air 口只用于 RF，实为 {q.signal}"
