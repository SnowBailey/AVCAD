"""会讨手拉手链路（阳哥 2026-08-30，太阳纸业 1F 反馈「连线逻辑有问题」）。

官方（ezpro CF63 系列）：
  · 会议主机与有线会议单元、天线盒均采用**专用六芯主缆**连接；
  · 有线系统单元间采用 **T 型线**连接；
  · 支持环形手拉手，某台单元故障不影响整套系统。

★ 每只单元就是**一进一出**：上一只 DIN_OUT → 本只 DIN_IN；
  链上最靠近主机的一只 DIN_OUT → 主机的一个 CH 六芯主缆口。

历史缺陷：主库里 DIN 的 in/out **同名同号**（都展开成 ``DIN_1``），
端口 ID 撞车，渲染时认不出「一进一出」，链看着是断的。
修复：主库把进口改名 ``DIN_IN``、出口改名 ``DIN_OUT``，
并新增 ``CONF`` 信号（六芯主缆 / T 型线专用，与 XLR 区分开）。
"""
from __future__ import annotations

import collections

from avcad.core.build import build_project
from avcad.model.schema import Signal


def _host(model="CF6300"):
    return {"category": "MIC_HOST", "brand": "IPS", "model": model,
            "name": "有线无线融合会议主机", "quantity": 1}


def _unit(model="CF6320", n=1):
    return [{"category": "SOURCE", "brand": "IPS", "model": model,
             "name": "会议单元", "quantity": 1,
             "params": {"host": "CF6300"}} for _ in range(n)]


def _conf_links(p):
    return [c for c in p.connections if c.signal == Signal.CONF]


def test_units_form_single_chain_one_in_one_out():
    """5 只单元串成 1 条链：4 条 T 型线 + 1 条六芯主缆进主机。"""
    p = build_project([_host(), *_unit(n=5)], name="T")
    links = _conf_links(p)
    assert len(links) == 5, f"应 5 条会议线（4 T型 + 1 主缆），实为 {len(links)}"

    to_host = [c for c in links if "主机" in (c.note or "")]
    assert len(to_host) == 1, f"进主机的主缆应 1 条，实为 {len(to_host)}"

    # 每只单元：进线 ≤1、出线 ≤1（一进一出）
    indeg = collections.Counter(c.to_uid for c in links)
    outdeg = collections.Counter(c.from_uid for c in links)
    for u in p.instances:
        if u.category == "MIC_HOST":
            continue
        assert indeg[u.uid] <= 1, f"{u.model} 有 {indeg[u.uid]} 条进线"
        assert outdeg[u.uid] <= 1, f"{u.model} 有 {outdeg[u.uid]} 条出线"


def test_conf_port_ids_do_not_collide():
    """进口 DIN_IN 与出口 DIN_OUT 必须展开成不同的端口 ID。"""
    p = build_project([_host(), *_unit(n=3)], name="T")
    for u in p.instances:
        if u.category == "MIC_HOST":
            continue
        ids = [pp.id for pp in u.ports]
        assert len(ids) == len(set(ids)), f"{u.model} 端口 ID 撞车：{ids}"
        din_in = [pp for pp in u.ports if pp.role == "in" and pp.signal == Signal.CONF]
        din_out = [pp for pp in u.ports if pp.role == "out" and pp.signal == Signal.CONF]
        assert din_in and din_out, f"{u.model} 缺 CONF 进/出口"
        assert din_in[0].id != din_out[0].id, \
            f"{u.model} 进/出口同名同号：{din_in[0].id}"


def test_chain_count_never_exceeds_host_buses():
    """链数不能超过主机的六芯主缆总线口数。"""
    p = build_project([_host(), *_unit(n=30)], name="T")
    host = next(i for i in p.instances if i.category == "MIC_HOST")
    host_port_ids = {pp.id for pp in host.ports}
    # 只统计「链尾 → 主机」的那些线
    to_host = [c for c in _conf_links(p) if c.to_uid == host.uid]
    assert to_host, "没有任何链汇入主机"
    assert {c.to_port for c in to_host} <= host_port_ids, \
        "链尾接到了主机不存在的端口上"
    # 每个 CH 口最多挂一条链，不得复用
    ch_ports = [c.to_port for c in to_host]
    assert len(ch_ports) == len(set(ch_ports)), f"主机 CH 口被复用：{ch_ports}"


def test_conference_units_excluded_from_star_pairing():
    """已串成手拉手的单元不再参与 SOURCE 的星型配对（不直连调音台）。"""
    mixer = {"category": "MIXER", "brand": "ALLEN&HEATH", "model": "QU-16",
             "name": "调音台", "quantity": 1,
             "params": {"inputs": 16, "outputs": 8}}
    p = build_project([_host(), *_unit(n=4), mixer], name="T")
    unit_uids = {i.uid for i in p.instances
                 if i.category != "MIC_HOST" and i.category != "MIXER"}
    mixer_uid = next(i.uid for i in p.instances if i.category == "MIXER")
    for c in p.connections:
        if c.from_uid in unit_uids and c.to_uid == mixer_uid:
            raise AssertionError(
                f"会议单元 {c.from_uid} 直连调音台了，应统一经主机汇入")
