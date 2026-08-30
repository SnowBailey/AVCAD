import os
import ezdxf
import pytest
from avcad.parse.bom_parser import parse_bom
from avcad.parse.product_resolver import resolve
from avcad.core.build import build_project, generate_candidates
from avcad.render.draw import draw_devices, draw_wires
from avcad.render.primitives import Canvas
from avcad.render.dxf_render import render_dxf
from avcad.wires.amp_match import match_speakers_to_amp
from avcad.model.schema import DeviceInstance, Signal, Redundancy

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample_bom.csv")


def _proj():
    return build_project(parse_bom(SAMPLE), name="T")


def test_parse_sample():
    e = parse_bom(SAMPLE)
    assert len(e) == 9
    active = [x for x in e if str(x.get("category")) == "SPEAKER" and x.get("active")]
    assert active and active[0]["active"] is True


def test_resolver():
    e = {"brand": "Shure", "model": "ULXD4D", "category": "WIRELESS_RX"}
    resolve(e)
    assert "dante" in e["features"] and "control" in e["features"]
    assert e["params"]["channels"] == 2


def test_build_basic():
    p = _proj()
    errs = [i for i in p.issues if i.level == "ERROR"]
    assert not errs, [i.msg for i in errs]
    assert p.switches  # 含 Dante -> 有交换机
    assert len(p.connections) > 10
    # WIRELESS_RX 作为音源输出端，在链路上与 SOURCE 同列
    assert "SOURCE" in p.chain and "MIXER" in p.chain
    rx = [i for i in p.instances if i.category == "WIRELESS_RX"]
    assert rx and all(i.stage == "SOURCE" for i in rx)


def test_wireless_diversity():
    p = _proj()
    rx = [i for i in p.instances if i.category == "WIRELESS_RX"]
    assert rx
    for r in rx:
        ant = [pt for pt in r.ports if pt.signal == Signal.RF and pt.role == "in"]
        assert len(ant) >= 2


def test_ports_override_as_string_does_not_crash():
    """★ 回归：ports_override 被压成字符串（旧 CSV / Python repr）不能让出图崩溃。

    历史 bug：遍历字符串得到单个字符 -> t.get() 抛
    'str' object has no attribute 'get'。
    """
    override = [{"name": "PHX", "side": "right", "signal": "XLR",
                 "role": "out", "label": "PHX", "count": 4}]
    entries = [{
        "category": "IO", "brand": "IPS", "model": "CF6300",
        "name": "会议主机", "quantity": 1, "features": [],
        "params": {"ports_override": str(override)},   # 模拟旧 CSV 的 Python repr
    }]
    p = build_project(entries, name="T")
    inst = [i for i in p.instances if i.model == "CF6300"]
    assert inst
    # Python repr 可被 literal_eval 还原 -> 端口按 override 展开
    assert len(inst[0].ports) == 4


def test_ports_override_unparsable_keeps_template():
    """无法解析的 ports_override 保留模板端口，不静默清空设备端口。"""
    entries = [{
        "category": "IO", "brand": "X", "model": "JUNK",
        "name": "坏数据", "quantity": 1, "features": [],
        "params": {"ports_override": "???不是JSON也不是repr???"},
    }]
    p = build_project(entries, name="T")
    inst = [i for i in p.instances if i.model == "JUNK"]
    assert inst and len(inst[0].ports) > 0   # 回退到 IO 模板端口


def test_slots_as_string_is_not_exploded():
    """slots 被压成字符串时，不能按字符展开成一堆单字母卡槽。"""
    slots = [{"type": "HY", "count": 2, "label": "HY"}]
    entries = [{
        "category": "MIXER", "brand": "Yamaha", "model": "CS-R10",
        "name": "调音台", "quantity": 1, "features": [],
        "params": {"slots": str(slots)},
    }]
    p = build_project(entries, name="T")
    inst = [i for i in p.instances if i.model == "CS-R10"]
    assert inst
    assert len(inst[0].slots) == 1, f"slots 被错误展开: {inst[0].slots}"


def test_candidates_redundancy():
    cands = generate_candidates(parse_bom(SAMPLE))
    assert len(cands) == 3
    t3 = cands[2][1]
    assert t3 is not None
    assert len(t3.switches) == 2
    assert any(c.role == "backup" for c in t3.connections)
    # T1 无主备
    assert all(c.role == "primary" for c in cands[0][1].connections)


def test_amp_match_prefer_independent():
    # 优先 8Ω 独立通道：2 通道 / 2 只 8Ω → 每通道各 1 只，独立 8Ω
    amp = DeviceInstance(uid="a", category="AMP", name="功放",
                          features={"analog"}, params={"channels": 2},
                          electrical={"min_load_ohm": 4, "power_w_per_ch": 1200})
    spk8 = [DeviceInstance(uid=f"s{i}", category="SPEAKER", name="箱",
                           params={"impedance_ohm": 8, "power_w": 200}) for i in range(2)]
    res = match_speakers_to_amp(amp, spk8)
    assert res[0][2] == "independent" and res[0][3] == 8 and res[0][4]
    assert res[1][2] == "independent" and res[1][3] == 8 and res[1][4]


def test_amp_match_parallel_when_short():
    # 通道不足才并联：1 通道 / 2 只 8Ω → 并联 4Ω（≥4Ω 安全）
    amp = DeviceInstance(uid="a", category="AMP", name="功放",
                          features={"analog"}, params={"channels": 1},
                          electrical={"min_load_ohm": 4, "power_w_per_ch": 1200})
    spk8 = [DeviceInstance(uid=f"s{i}", category="SPEAKER", name="箱",
                           params={"impedance_ohm": 8, "power_w": 200}) for i in range(2)]
    res = match_speakers_to_amp(amp, spk8)
    assert res[0][2] == "parallel" and res[0][3] == 4.0 and res[0][4]


def test_amp_match_series_fallback():
    # 并联越限回退串联：1 通道 / 2 只 4Ω → 并联 2Ω<4Ω 越限 → 串联 8Ω
    amp = DeviceInstance(uid="a", category="AMP", name="功放",
                          features={"analog"}, params={"channels": 1},
                          electrical={"min_load_ohm": 4, "power_w_per_ch": 1200})
    spk4 = [DeviceInstance(uid=f"t{i}", category="SPEAKER", name="箱",
                           params={"impedance_ohm": 4, "power_w": 200}) for i in range(2)]
    res2 = match_speakers_to_amp(amp, spk4)
    assert res2[0][2] == "series"


def test_dxf_roundtrip():
    p = _proj()
    c = Canvas(); draw_devices(c, p); draw_wires(c, p)
    import tempfile
    fn = tempfile.mktemp(suffix=".dxf")
    render_dxf(c, fn, p.name)
    d = ezdxf.readfile(fn)
    assert sum(1 for _ in d.modelspace()) > 50
    os.unlink(fn)


def test_active_speaker_no_amp_link():
    # 全有源系统：应跳过 AMP 阶段
    e = [
        {"category": "MIXER", "features": ["dante"], "params": {"inputs": 8, "outputs": 4}},
        {"category": "SPEAKER", "active": True, "features": ["dante"], "params": {"impedance_ohm": 8}},
    ]
    p = build_project(e)
    assert "AMP" not in p.chain
    assert any(i.category == "SPEAKER" and i.active for i in p.instances)
