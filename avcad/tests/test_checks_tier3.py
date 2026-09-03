"""Tier 3 音频/网络理解优化的回归测试（能力门控 + 字段门控，零误报）。

全部用最小桩 DeviceInstance 直接喂 validate()。今天主库未标任何 Tier 3 标记
（aec/delay/vc/ptp/zone 等 features 与 line_type/tap_w/f_x/ptp_role/zone 等
params 均不存在）→ 全休眠；标了设备自动生效。每条均含 fire + 负向防误报用例。
"""
from __future__ import annotations

from avcad.model.schema import (
    Signal, Project, DeviceInstance, Port, Connection,
)
from avcad.validate.checks import validate


def _dev(uid, cat, active=False, features=(), params=None,
         out=(), in_=(), sw=False):
    ports = (
        [Port(id=f"o{i}", side="right", signal=s, label=f"O{i}", role="out")
         for i, s in enumerate(out)]
        + [Port(id=f"i{i}", side="left", signal=s, label=f"I{i}", role="in")
         for i, s in enumerate(in_)]
    )
    cls = "SWITCH" if sw else cat
    return DeviceInstance(
        uid=uid, category=cls, name=uid, model=uid,
        active=active, features=set(features),
        params=params or {}, ports=ports,
    )


# ─────────────────────────── AEC_MISSING（needs_aec 门控）
def test_aec_missing_fires():
    """设备声明 needs_aec 但系统无 aec DSP → WARN。"""
    mic = _dev("MIC1", "SOURCE", params={"needs_aec": True}, in_=(Signal.XLR,))
    pro = _dev("DSP1", "PROCESSOR", features=("dante",))  # 无 aec
    proj = Project(instances=[mic, pro], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "AEC_MISSING" in codes, codes


def test_aec_missing_silent_without_flag():
    """未声明 needs_aec → 门控不触发。"""
    mic = _dev("MIC1", "SOURCE", in_=(Signal.XLR,))
    proj = Project(instances=[mic], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "AEC_MISSING" not in codes, codes


# ─────────────────────────── AEC_REF_UNCONNECTED（aec 能力门控）
def test_aec_ref_unconnected_fires():
    """DSP 声明 aec + 远端终端存在，但参考输入口未连 → ERROR。"""
    dsp = _dev("DSP1", "PROCESSOR", features=("aec",), in_=(Signal.XLR,))
    term = _dev("VC1", "IO", features=("vc",), out=(Signal.XLR,))  # 远端终端
    proj = Project(instances=[dsp, term], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "AEC_REF_UNCONNECTED" in codes, codes


def test_aec_ref_unconnected_silent_when_connected():
    """参考输入口已接到远端终端 → 不误报。"""
    dsp = _dev("DSP1", "PROCESSOR", features=("aec",), in_=(Signal.XLR,))
    term = _dev("VC1", "IO", features=("vc",), out=(Signal.XLR,))
    conns = [Connection("VC1", "o0", "DSP1", "i0", Signal.XLR, "primary")]
    proj = Project(instances=[dsp, term], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "AEC_REF_UNCONNECTED" not in codes, codes


def test_aec_ref_unconnected_silent_without_remote():
    """无远端终端（非会议系统）→ 门控不触发。"""
    dsp = _dev("DSP1", "PROCESSOR", features=("aec",), in_=(Signal.XLR,))
    proj = Project(instances=[dsp], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "AEC_REF_UNCONNECTED" not in codes, codes


# ─────────────────────────── DELAY_CAPABILITY_MISSING（fill 门控）
def test_delay_capability_missing_fires():
    """补声音箱(fill) 上游处理器无 delay 能力 → ERROR。"""
    spk = _dev("SPK1", "SPEAKER", params={"fill": True}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    pro = _dev("DSP1", "PROCESSOR", out=(Signal.XLR,))  # 无 delay
    conns = [
        Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary"),
        Connection("DSP1", "o0", "AMP1", "i0", Signal.XLR, "primary"),
    ]
    proj = Project(instances=[spk, amp, pro], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "DELAY_CAPABILITY_MISSING" in codes, codes


def test_delay_capability_missing_silent_when_processor_has_delay():
    """上游处理器带 delay 能力 → 不误报。"""
    spk = _dev("SPK1", "SPEAKER", params={"fill": True}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    pro = _dev("DSP1", "PROCESSOR", features=("delay",), out=(Signal.XLR,))
    conns = [
        Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary"),
        Connection("DSP1", "o0", "AMP1", "i0", Signal.XLR, "primary"),
    ]
    proj = Project(instances=[spk, amp, pro], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "DELAY_CAPABILITY_MISSING" not in codes, codes


# ─────────────────────────── PTP_GM_NONE（ptp_role 门控）
def test_ptp_gm_none_fires():
    """声明 ptp_role 但无 gm/boundary → WARN。"""
    sw = _dev("SW1", "SWITCH", sw=True, out=(Signal.DANTE,), in_=(Signal.DANTE,))
    pro = _dev("DSP1", "PROCESSOR", params={"ptp_role": "slave"},
               out=(Signal.DANTE,), in_=(Signal.DANTE,))
    proj = Project(instances=[sw, pro], connections=[
        Connection("DSP1", "o0", "SW1", "i0", Signal.DANTE, "primary"),
    ])
    codes = [i.code for i in validate(proj)]
    assert "PTP_GM_NONE" in codes, codes


def test_ptp_gm_none_silent_when_gm_present():
    """有设备声明 ptp_role=gm → 不误报。"""
    sw = _dev("SW1", "SWITCH", sw=True, out=(Signal.DANTE,), in_=(Signal.DANTE,))
    pro = _dev("DSP1", "PROCESSOR", params={"ptp_role": "gm"},
               out=(Signal.DANTE,), in_=(Signal.DANTE,))
    proj = Project(instances=[sw, pro], connections=[
        Connection("DSP1", "o0", "SW1", "i0", Signal.DANTE, "primary"),
    ])
    codes = [i.code for i in validate(proj)]
    assert "PTP_GM_NONE" not in codes, codes


# ─────────────────────────── CV_ 定压（line_type 字段门控）
def test_cv_mixed_fires():
    """定压音箱(line_type=70v) 挂低阻功放 → ERROR。"""
    spk = _dev("SPK1", "SPEAKER", params={"line_type": "70v"}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))  # 无 line_type → 低阻
    conns = [Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[spk, amp], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "CV_MIXED" in codes, codes


def test_cv_mixed_silent_on_cv_amp():
    """定压音箱 + 定压功放 → 正常，不误报。"""
    spk = _dev("SPK1", "SPEAKER", params={"line_type": "70v"}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", params={"line_type": "70v"}, out=(Signal.SPEAKER,))
    conns = [Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[spk, amp], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "CV_MIXED" not in codes, codes
    assert "CV_VOLTAGE_MISMATCH" not in codes, codes


def test_cv_voltage_mismatch_fires():
    """70V 音箱接 100V 功放 → ERROR。"""
    spk = _dev("SPK1", "SPEAKER", params={"line_type": "70v"}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", params={"line_type": "100v"}, out=(Signal.SPEAKER,))
    conns = [Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[spk, amp], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "CV_VOLTAGE_MISMATCH" in codes, codes


# ─────────────────────────── XOVER_GAP（f_x 字段门控）
def test_xover_gap_fires():
    """f_x 声明为单点（无效）→ ERROR。"""
    spk = _dev("SPK1", "SPEAKER", params={"f_x": [1000]}, in_=(Signal.SPEAKER,))
    proj = Project(instances=[spk], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "XOVER_GAP" in codes, codes


def test_xover_gap_silent_when_valid():
    """f_x 为有效两点 → 不误报。"""
    spk = _dev("SPK1", "SPEAKER", params={"f_x": [1000, 5000]}, in_=(Signal.SPEAKER,))
    proj = Project(instances=[spk], connections=[])
    codes = [i.code for i in validate(proj)]
    assert "XOVER_GAP" not in codes, codes


# ─────────────────────────── ZONE_SINGLE_AMP（zone 字段门控）
def test_zone_single_amp_fires():
    """同分区两音箱全挂 1 台功放 → WARN。"""
    s1 = _dev("SPK1", "SPEAKER", params={"zone": "A"}, in_=(Signal.SPEAKER,))
    s2 = _dev("SPK2", "SPEAKER", params={"zone": "A"}, in_=(Signal.SPEAKER,))
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    conns = [
        Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary"),
        Connection("AMP1", "o0", "SPK2", "i0", Signal.SPEAKER, "primary"),
    ]
    proj = Project(instances=[s1, s2, amp], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "ZONE_SINGLE_AMP" in codes, codes


def test_zone_single_amp_silent_on_two_amps():
    """同分区两音箱分别挂 2 台功放 → 不误报。"""
    s1 = _dev("SPK1", "SPEAKER", params={"zone": "A"}, in_=(Signal.SPEAKER,))
    s2 = _dev("SPK2", "SPEAKER", params={"zone": "A"}, in_=(Signal.SPEAKER,))
    a1 = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    a2 = _dev("AMP2", "AMP", out=(Signal.SPEAKER,))
    conns = [
        Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary"),
        Connection("AMP2", "o0", "SPK2", "i0", Signal.SPEAKER, "primary"),
    ]
    proj = Project(instances=[s1, s2, a1, a2], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "ZONE_SINGLE_AMP" not in codes, codes
