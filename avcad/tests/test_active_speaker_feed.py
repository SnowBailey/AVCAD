"""有源扬声器取信号规则（阳哥 2026-08-30，太阳纸业 1F 反馈）。

缺陷复现：BF12 有源音箱出现两个问题——
  1. 信号取自调音台，但调音台**没有多出输出线**（前级出口被音响管理器占着，
     有源分支复用了同一批端口，两根线压在一个口上）；
  2. 图上**多出两根横向线**（_dante_pass 又给每台有源音箱补了一条 Dante）。

定下来的规则：
  A. 有源音箱只取**一路**信号，且线的信号类型必须与它落的端口一致；
  B. 只能接前级**空闲**的模拟出口，绝不复用已占用的端口；
  C. 同一方案里有源音箱的取信号方式要统一：模拟出口够喂全部 → 全模拟；
     不够但都能走 Dante 且有交换机 → 全 Dante（经交换机承载）；
  D. 已走模拟的有源音箱，_dante_pass 不再给它补 Dante。
"""
from __future__ import annotations

import collections

from avcad.core.build import build_project
from avcad.model.schema import Signal

ANALOG = (Signal.XLR, Signal.AES)


def _mixer(outputs=8, inputs=16, uid="mixer_1"):
    return {"category": "MIXER", "brand": "ALLEN&HEATH", "model": "QU-16",
            "name": "调音台", "quantity": 1,
            "params": {"inputs": inputs, "outputs": outputs}}


def _spk_mgr(inputs=4, outputs=8):
    return {"category": "SPEAKER_MGR", "brand": "IPS", "model": "FIRM4×8",
            "name": "音箱管理器", "quantity": 1,
            "params": {"inputs": inputs, "outputs": outputs}}


def _amp(ch=2):
    return {"category": "AMP", "brand": "IPS", "model": "DA250",
            "name": "功放", "quantity": 1,
            "features": ["analog"], "params": {"channels": ch}}


def _active_spk(n=2, dante=True, uid_prefix="spk"):
    feats = ["control"] + (["dante"] if dante else [])
    return [{"category": "SPEAKER", "brand": "IPS", "model": "BF12",
             "name": "有源音箱", "quantity": 1, "active": True,
             "features": feats, "params": {"power_w": 600}}
            for _ in range(n)]


def _passive_spk(n=4):
    return [{"category": "SPEAKER", "brand": "IPS", "model": "CI600",
             "name": "吸顶音箱", "quantity": 1, "active": False,
             "features": [], "params": {"impedance_ohm": 8, "power_w": 60}}
            for _ in range(n)]


def _feeds(proj):
    """返回 {音箱uid: [Connection,...]}，只统计音频进线。"""
    cat = {i.uid: i.category for i in proj.instances + proj.switches}
    out = collections.defaultdict(list)
    for c in proj.connections:
        if cat.get(c.to_uid) == "SPEAKER" and \
                c.signal in (*ANALOG, Signal.DANTE):
            out[c.to_uid].append(c)
    return out


def test_active_speaker_takes_exactly_one_analog_feed():
    """管理器占着 OUT1~4，有源音箱必须用空闲的 OUT5/OUT6。"""
    entries = [_mixer(), _spk_mgr(), _amp(), *_active_spk(2), *_passive_spk(4)]
    p = build_project(entries, name="T")
    actives = [i for i in p.instances if i.category == "SPEAKER" and i.active]
    assert len(actives) == 2

    feeds = _feeds(p)
    # A. 每台有源音箱恰好一路
    for s in actives:
        assert len(feeds[s.uid]) == 1, f"{s.model} 取了 {len(feeds[s.uid])} 路"
        # 信号类型与落点端口一致
        c = feeds[s.uid][0]
        port = next(x for x in s.ports if x.id == c.to_port)
        assert port.signal == c.signal
        assert c.signal in ANALOG  # 模拟优先

    mixer = next(i for i in p.instances if i.category == "MIXER")
    used = [c.from_port for c in p.connections if c.from_uid == mixer.uid]
    # B. 出口绝不复用（8 出口：管理器 4 + 有源 2，无重复）
    assert len(used) == len(set(used)), f"调音台出口被复用：{used}"
    assert len(used) == 6, used
    # 有源音箱必须接到管理器之后的新出口，而不是压在 OUT1~4 上
    for s in actives:
        assert feeds[s.uid][0].from_port not in {"%s:OUT_1" % mixer.uid}


def test_active_speaker_never_gets_both_analog_and_dante():
    """有 Dante 交换机时，已走模拟的有源音箱不能再补 Dante 线。"""
    entries = [{"category": "SWITCH", "brand": "HUAWEI", "model": "千兆交换机",
                "name": "交换机", "quantity": 1,
                "params": {"ports": 24}, "features": []},
               _mixer(outputs=8), *_active_spk(2, dante=True)]
    p = build_project(entries, name="T")
    assert p.switches
    feeds = _feeds(p)
    for s in (i for i in p.instances if i.category == "SPEAKER"):
        assert len(feeds[s.uid]) <= 1, \
            f"{s.model} 同时取了 {[c.signal.value for c in feeds[s.uid]]}"


def test_all_dante_when_analog_outs_insufficient():
    """模拟出口不够喂全部有源音箱 → 统一走 Dante，不要一半模拟一半 Dante。"""
    entries = [{"category": "SWITCH", "brand": "HUAWEI", "model": "千兆交换机",
                "name": "交换机", "quantity": 1,
                "params": {"ports": 24}, "features": []},
               _mixer(outputs=2), *_active_spk(8, dante=True)]
    p = build_project(entries, name="T")
    feeds = _feeds(p)
    sigs = collections.Counter()
    for s in (i for i in p.instances if i.category == "SPEAKER"):
        assert len(feeds[s.uid]) == 1
        sigs[feeds[s.uid][0].signal] += 1
    assert set(sigs) == {Signal.DANTE}, f"信号类型不统一：{sigs}"
    assert sigs[Signal.DANTE] == 8


def test_dante_feed_always_comes_from_switch():
    """Dante 一律经交换机承载，禁止设备间直连（含扬声器）。"""
    entries = [{"category": "SWITCH", "brand": "HUAWEI", "model": "千兆交换机",
                "name": "交换机", "quantity": 1,
                "params": {"ports": 24}, "features": []},
               _mixer(outputs=2), *_active_spk(4, dante=True)]
    p = build_project(entries, name="T")
    cat = {i.uid: i.category for i in p.instances + p.switches}
    for c in p.connections:
        if c.signal != Signal.DANTE:
            continue
        ends = (cat.get(c.from_uid), cat.get(c.to_uid))
        assert "SWITCH" in ends, f"Dante 线未经过交换机：{ends}"


def test_no_prev_stage_device_ports_double_booked():
    """全局：任何设备的模拟/Dante 出口都不允许被两条线复用。"""
    entries = [_mixer(outputs=8), _spk_mgr(), _amp(ch=4), _amp(ch=2),
               *_active_spk(2), *_passive_spk(8)]
    p = build_project(entries, name="T")
    cnt = collections.Counter()
    for c in p.connections:
        if c.signal in (*ANALOG, Signal.DANTE):
            cnt[(c.from_uid, c.from_port)] += 1
    dup = {k: v for k, v in cnt.items() if v > 1}
    assert not dup, f"出口被复用：{dup}"
