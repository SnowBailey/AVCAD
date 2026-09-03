"""S1 / S2 静默拓扑错误的根因修复回归测试。

S1：天线分配器出口不足时，接不到的接收机必须全部进入「出口不足」告警，
    不能因为循环中先 pop 再 break 而静默丢失（既不连线也不告警）。
S2：两个不同信号源接入同一设备输入口（双源冲突）在物理上不可行，
    _claim_input 在落线源头拦截，并产出 double_source_warnings。
"""
from __future__ import annotations

import re

from avcad.core.build import build_project
from avcad.model.schema import DeviceInstance, ConcretePort, Signal, Project
from avcad.wires.router import _generic_pair


# ---------------- S2：双源冲突拦截 ----------------
def _dev(uid, cat, ports):
    return DeviceInstance(uid=uid, category=cat, name=cat, ports=ports)


def _port(uid, pid, sig, role):
    return ConcretePort(id=pid, uid=uid, side="left", signal=sig,
                        label=pid, role=role, air=False)


def _proj(devs):
    p = Project(instances=list(devs), connections=[], meta={})
    p._used_in = {}
    return p


def test_double_source_blocked_on_same_input():
    """两个 SOURCE 分两次配对到同一 MIXER 输入口：第二次应被拦截并告警。"""
    mixer = _dev("M1", "MIXER", [_port("M1", "IN1", Signal.XLR, "in")])
    s1 = _dev("S1", "SOURCE", [_port("S1", "OUT1", Signal.XLR, "out")])
    s2 = _dev("S2", "SOURCE", [_port("S2", "OUT1", Signal.XLR, "out")])
    p = _proj([s1, s2, mixer])
    _generic_pair(p, [s1], [mixer])     # 步骤1：S1 占 IN1
    _generic_pair(p, [s2], [mixer])     # 步骤2：S2 想占同一个 IN1 -> 应被拦
    assert len(p.connections) == 1, [c.__dict__ for c in p.connections]
    assert p.connections[0].from_uid == "S1"
    warns = p.meta.get("double_source_warnings", [])
    assert len(warns) == 1, warns


def test_distinct_inputs_no_false_warning():
    """同一次配对里多源对应多输入口（1:1），正常落线、不误报。"""
    mixer = _dev("M1", "MIXER", [
        _port("M1", "IN1", Signal.XLR, "in"),
        _port("M1", "IN2", Signal.XLR, "in"),
    ])
    s1 = _dev("S1", "SOURCE", [_port("S1", "OUT1", Signal.XLR, "out")])
    s2 = _dev("S2", "SOURCE", [_port("S2", "OUT1", Signal.XLR, "out")])
    p = _proj([s1, s2, mixer])
    _generic_pair(p, [s1, s2], [mixer])   # 一次配对：S1->IN1, S2->IN2
    assert len(p.connections) == 2
    assert not p.meta.get("double_source_warnings")


# ---------------- S1：天线分配不静默丢失 ----------------
def _rx(q=5):
    return {"category": "WIRELESS_RX", "brand": "IPS", "model": "UM2002",
            "name": "无线接收机", "quantity": q, "features": [],
            "params": {"channels": 2, "antennas": 4}}


def _dist():
    return {"category": "ANT_DIST", "brand": "IPS", "model": "UM2000ATD",
            "name": "天线分配器", "quantity": 1,
            "params": {"inputs": 2, "outputs": 10}}


def _mixer():
    return {"category": "MIXER", "brand": "IPS", "model": "M16",
            "name": "调音台", "quantity": 1, "params": {"inputs": 16, "outputs": 8}}


def test_antenna_drop_no_silent_loss():
    """S1：单台分配器（10 出）接 5 台接收机（每台 4 口）。

    2 台完整接入、3 台接不到；3 台必须全部进入「出口不足」告警，
    旧代码会把其中 1 台（触发 break 的那台）从 pending 静默移除 -> 漏报。
    """
    p = build_project([_dist(), _rx(5), _mixer()], name="S1")
    feeds = [c for c in p.connections if c.note == "天线分配"]
    connected_rx = {c.to_uid for c in feeds}
    # 每台占 4 口；10 出 -> 2 台完整接入
    assert len(connected_rx) == 2, len(connected_rx)
    warns = [w for w in (p.meta.get("wireless_warnings") or [])
             if "未分配到天线口" in w]
    assert warns, "出口不足应产生告警"
    m = re.search(r"出口不足：(\d+) 台", warns[0])
    assert m, warns[0]
    unconnected = int(m.group(1))
    # 关键不变量：已接入 + 告警未接入 == 总接收机数（旧代码会少算 1 台）
    assert len(connected_rx) + unconnected == 5, (len(connected_rx), unconnected)
