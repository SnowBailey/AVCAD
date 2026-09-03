"""Tier 2 音频理解优化的回归测试（零新字段 / 低误报）。

ACTIVE_ON_AMP_OUT：有源音箱(active=True)接 SPEAKER 缆 → ERROR。
LEVEL_DOMAIN      ：扬声器线缆(SPEAKER)两端非「功放/音响管理器→无源音箱」配对 → ERROR。
PHANTOM_MISSING   ：受 needs_phantom 标志门控——设备需 P48 且上游 XLR 无 phantom 提供 → ERROR。

全部用最小桩 DeviceInstance 直接喂 validate()，不依赖主库量化数据。
"""
from __future__ import annotations

from avcad.model.schema import (
    Signal, Project, DeviceInstance, Port, Connection,
)
from avcad.validate.checks import validate


def _dev(uid, cat, active=False, features=(), params=None,
         out=(), in_=()):
    """构造一台最小桩设备（out/in 为 signal 列表）。"""
    ports = (
        [Port(id=f"o{i}", side="right", signal=s, label=f"O{i}", role="out")
         for i, s in enumerate(out)]
        + [Port(id=f"i{i}", side="left", signal=s, label=f"I{i}", role="in")
         for i, s in enumerate(in_)]
    )
    return DeviceInstance(
        uid=uid, category=cat, name=uid, model=uid,
        active=active, features=set(features),
        params=params or {}, ports=ports,
    )


# ─────────────────────────── ACTIVE_ON_AMP_OUT
def test_active_speaker_on_amp_speaker_out_fires():
    """有源音箱接功放 SPEAKER 输出 → 必须报 ACTIVE_ON_AMP_OUT。"""
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    spk = _dev("SPK1", "SPEAKER", active=True, in_=(Signal.SPEAKER,))
    conns = [Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[amp, spk], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "ACTIVE_ON_AMP_OUT" in codes, codes


def test_passive_speaker_on_amp_speaker_out_silent():
    """无源音箱接功放 SPEAKER 输出 → 正常，不误报。"""
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    spk = _dev("SPK1", "SPEAKER", active=False, in_=(Signal.SPEAKER,))
    conns = [Connection("AMP1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[amp, spk], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "ACTIVE_ON_AMP_OUT" not in codes, codes
    assert "LEVEL_DOMAIN" not in codes, codes


# ─────────────────────────── LEVEL_DOMAIN
def test_speaker_cable_into_line_device_fires():
    """功放 SPEAKER 缆误接 MIXER（线路设备）→ 必须报 LEVEL_DOMAIN。"""
    amp = _dev("AMP1", "AMP", out=(Signal.SPEAKER,))
    mix = _dev("MIX1", "MIXER", in_=(Signal.SPEAKER,))
    conns = [Connection("AMP1", "o0", "MIX1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[amp, mix], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "LEVEL_DOMAIN" in codes, codes


def test_speaker_cable_from_non_amp_fires():
    """SOURCE 直接出 SPEAKER 缆接无源音箱（线缆源自非功放）→ 必须报 LEVEL_DOMAIN。"""
    src = _dev("SRC1", "SOURCE", out=(Signal.SPEAKER,))
    spk = _dev("SPK1", "SPEAKER", active=False, in_=(Signal.SPEAKER,))
    conns = [Connection("SRC1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[src, spk], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "LEVEL_DOMAIN" in codes, codes


def test_speakermgr_to_passive_speaker_silent():
    """音响管理器 → 无源音箱（正确配对）→ 不误报。"""
    mgr = _dev("MGR1", "SPEAKER_MGR", out=(Signal.SPEAKER,))
    spk = _dev("SPK1", "SPEAKER", active=False, in_=(Signal.SPEAKER,))
    conns = [Connection("MGR1", "o0", "SPK1", "i0", Signal.SPEAKER, "primary")]
    proj = Project(instances=[mgr, spk], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "LEVEL_DOMAIN" not in codes, codes
    assert "ACTIVE_ON_AMP_OUT" not in codes, codes


# ─────────────────────────── PHANTOM_MISSING（门控）
def test_phantom_missing_fires_when_upstream_no_phantom():
    """电容麦(needs_phantom) 接上游 XLR 但不提供 phantom → 必须报 PHANTOM_MISSING。"""
    mic = _dev("MIC1", "SOURCE", params={"needs_phantom": True},
               in_=(Signal.XLR,))
    io = _dev("IO1", "IO", features=("dante", "aes"), out=(Signal.XLR,))
    conns = [Connection("IO1", "o0", "MIC1", "i0", Signal.XLR, "primary")]
    proj = Project(instances=[mic, io], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "PHANTOM_MISSING" in codes, codes


def test_phantom_ok_when_upstream_provides():
    """上游标注提供 phantom → 不报。"""
    mic = _dev("MIC1", "SOURCE", params={"needs_phantom": True},
               in_=(Signal.XLR,))
    io = _dev("IO1", "IO", features=("dante", "aes", "phantom"),
              out=(Signal.XLR,))
    conns = [Connection("IO1", "o0", "MIC1", "i0", Signal.XLR, "primary")]
    proj = Project(instances=[mic, io], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "PHANTOM_MISSING" not in codes, codes


def test_phantom_silent_when_no_flag():
    """未声明 needs_phantom → 门控不触发（零误报）。"""
    mic = _dev("MIC1", "SOURCE", in_=(Signal.XLR,))
    io = _dev("IO1", "IO", features=("dante", "aes"), out=(Signal.XLR,))
    conns = [Connection("IO1", "o0", "MIC1", "i0", Signal.XLR, "primary")]
    proj = Project(instances=[mic, io], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "PHANTOM_MISSING" not in codes, codes
