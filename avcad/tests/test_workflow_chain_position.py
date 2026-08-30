"""处理器前置/后置链路逻辑（阳哥规则 2026-08-29）。"""
import pytest
from avcad.core.build import build_project
from avcad.topology.chain import PROC_PRE, PROC_POST


def _mk(rows):
    """rows: list of (category, params_dict, extra_dict)。"""
    entries = []
    for cat, params, extra in rows:
        e = {"category": cat, "name": cat, "quantity": 1,
             "params": dict(params or {})}
        e.update(extra or {})
        entries.append(e)
    return build_project(entries, name="T")


def _proc(p):
    return [i for i in p.instances if i.category == "PROCESSOR"]


def test_default_processor_is_pre():
    # 默认：处理器在调音台之前
    p = _mk([("SOURCE", {}, {}), ("PROCESSOR", {}, {}), ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_PRE in p.chain
    assert p.chain.index(PROC_PRE) < p.chain.index("MIXER")
    assert PROC_POST not in p.chain
    assert all(i.stage == PROC_PRE for i in _proc(p))


def test_explicit_post_position():
    p = _mk([("SOURCE", {}, {}), ("PROCESSOR", {"position": "后置"}, {}), ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_POST in p.chain
    assert p.chain.index("MIXER") < p.chain.index(PROC_POST)
    assert PROC_PRE not in p.chain
    assert all(i.stage == PROC_POST for i in _proc(p))


def test_amp_with_dsp_forces_pre():
    # 功放带 DSP -> 即便处理器写后置，也强制前置
    # 真实 eko 型号 X4 DSP+DANTE（型号含 DSP）；同时验证用户写 特性=dsp 也能留存
    p = _mk([("SOURCE", {}, {}), ("PROCESSOR", {"position": "后置"}, {}), ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"brand": "Powersoft", "model": "X4 DSP+DANTE",
              "features": ["dsp"], "electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    amp = [i for i in p.instances if i.category == "AMP"][0]
    assert "dsp" in {str(f).lower() for f in amp.features}, "功放 dsp 特性应被保留"
    assert PROC_PRE in p.chain
    assert all(i.stage == PROC_PRE for i in _proc(p))


def test_proc_func_semantics():
    p_auto = _mk([("SOURCE", {}, {}), ("PROCESSOR", {"proc_func": "automix"}, {}),
                  ("MIXER", {}, {}),
                  ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
                  ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_PRE in p_auto.chain

    p_sys = _mk([("SOURCE", {}, {}), ("PROCESSOR", {"proc_func": "system"}, {}),
                 ("MIXER", {}, {}),
                 ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
                 ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_POST in p_sys.chain and PROC_PRE not in p_sys.chain


def test_mixed_pre_post():
    # 同系统混排：一台前置 + 一台后置
    p = _mk([("SOURCE", {}, {}),
             ("PROCESSOR", {"position": "前置"}, {}),
             ("PROCESSOR", {"position": "后置"}, {}),
             ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_PRE in p.chain and PROC_POST in p.chain
    assert p.chain.index(PROC_PRE) < p.chain.index("MIXER") < p.chain.index(PROC_POST)
    pre = [i for i in _proc(p) if i.stage == PROC_PRE]
    post = [i for i in _proc(p) if i.stage == PROC_POST]
    assert pre and post


def test_redundant_processor_forced_pre():
    p = _mk([("SOURCE", {}, {}), ("PROCESSOR", {}, {"redundancy": "FULL_CHAIN"}),
             ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    assert PROC_PRE in p.chain
    assert all(i.stage == PROC_PRE for i in _proc(p))


def test_end_to_end_builds():
    p = _mk([("SOURCE", {}, {}), ("PROCESSOR", {}, {}), ("MIXER", {}, {}),
             ("AMP", {"channels": 2}, {"electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}),
             ("SPEAKER", {"impedance_ohm": 8}, {})])
    errs = [i for i in p.issues if i.level == "ERROR"]
    assert not errs, [i.msg for i in errs]
    assert len(p.connections) > 0
    # 源→处理器(前置)→调音台 信号流连通
    assert any(c.signal.value in ("XLR", "DANTE") for c in p.connections)
