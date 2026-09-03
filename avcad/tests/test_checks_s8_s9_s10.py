"""S7 / S8 / S9 / S10 静默错误修复的回归测试。

S7：图例库 save() 在打包只读目录下不得抛 PermissionError 使 /api/legend 保存 500；
    只读时应安全跳过落盘（内存记录仍在），并在默认库只读时重定向到用户可写目录。
S8：有 Dante 端口却零 Dante 连线（漏接 Dante 网络盲区）→ DANTE_NO_CONNECTION。
S9：声明了主备冗余却没画 failover 备份线（静默假冗余）→ REDUNDANT_NO_FAILOVER。
S10：SPOF_NET_NO_DUAL_SWITCH 改用冗余级别 scope 的 dual_switch 标志判定，
    消除 PROCESSOR_BACKUP（dual_switch=False）的设备级误报，同时 LINK/FULL_CHAIN
    （dual_switch=True）仍须触发。

全部零新字段，纯图遍历 / scope 查表。
"""
from __future__ import annotations

import os
import stat

import pytest

from avcad.model.schema import (
    Signal, Redundancy, Project, DeviceInstance, Port, Connection,
)
from avcad.validate.checks import validate
from avcad.workflow import legend_store
from avcad.workflow.legend_store import LegendStore, Legend, LegendPort, _resolve_legend_path


def _audio_dev(uid, cat, out_signals=(), in_signals=(),
               redundancy=Redundancy.NONE, is_backup=False, pair=None):
    """构造一台普通音频设备（最小桩，可直接喂给 validate）。"""
    ports = (
        [Port(id=f"o{i}", side="right", signal=s, label=f"O{i}", role="out")
         for i, s in enumerate(out_signals)]
        + [Port(id=f"i{i}", side="left", signal=s, label=f"I{i}", role="in")
            for i, s in enumerate(in_signals)]
    )
    return DeviceInstance(uid=uid, category=cat, name=uid, model=uid,
                          ports=ports, redundancy=redundancy,
                          is_backup=is_backup, pair=pair)


# ============================================================ S8：Dante 网络盲区
def test_dante_no_connection_fires_on_unconnected_dante_device():
    """B 有 Dante 端口却只走 XLR、零 Dante 连接；A 已接入 Dante 网络 → 只报 B。"""
    sw = _audio_dev("SW1", "SWITCH", out_signals=(Signal.DANTE,),
                    in_signals=(Signal.DANTE,))
    a = _audio_dev("A", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    b = _audio_dev("B", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    conns = [
        Connection("A", "o0", "SW1", "i0", Signal.DANTE, "primary"),  # A 接了 Dante
        Connection("B", "o0", "A", "i0", Signal.XLR, "primary"),      # B 仅 XLR，零 Dante 连接
    ]
    proj = Project(instances=[sw, a, b], connections=conns)
    issues = validate(proj)
    codes = [i.code for i in issues]
    assert "DANTE_NO_CONNECTION" in codes, codes
    assert any(i.ref == "B" for i in issues if i.code == "DANTE_NO_CONNECTION"), \
        "应只报零 Dante 连接的 B，而非已接入的 A"


def test_dante_no_connection_silent_when_all_dante_connected():
    """所有 Dante 设备都接上了 Dante 网络 → 不误报。"""
    sw = _audio_dev("SW1", "SWITCH", out_signals=(Signal.DANTE,),
                    in_signals=(Signal.DANTE,))
    a = _audio_dev("A", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    b = _audio_dev("B", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    conns = [
        Connection("A", "o0", "SW1", "i0", Signal.DANTE, "primary"),
        Connection("B", "o0", "SW1", "i0", Signal.DANTE, "primary"),
    ]
    proj = Project(instances=[sw, a, b], connections=conns)
    assert "DANTE_NO_CONNECTION" not in [i.code for i in validate(proj)]


def test_dante_no_connection_silent_when_no_dante_network_at_all():
    """整系统零 Dante 连线（纯模拟）→ 不触发（那属于全模拟，不在本刀范围）。"""
    a = _audio_dev("A", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    b = _audio_dev("B", "PROCESSOR", out_signals=(Signal.DANTE,),
                   in_signals=(Signal.DANTE,))
    conns = [Connection("A", "o0", "B", "i0", Signal.XLR, "primary")]
    proj = Project(instances=[a, b], connections=conns)
    assert "DANTE_NO_CONNECTION" not in [i.code for i in validate(proj)]


# ============================================================ S9：声明冗余却无 failover 线
def test_redundant_pair_without_failover_fires():
    """设了 pair + 需 failover_link 的冗余，却缺 role="backup" 连线 → 必须报。"""
    main = _audio_dev("P1", "PROCESSOR", out_signals=(Signal.XLR,),
                      in_signals=(Signal.XLR,),
                      redundancy=Redundancy.PROCESSOR_BACKUP, pair="P2")
    bak = _audio_dev("P2", "PROCESSOR", out_signals=(Signal.XLR,),
                     in_signals=(Signal.XLR,),
                     redundancy=Redundancy.PROCESSOR_BACKUP, is_backup=True, pair="P1")
    conns = [Connection("P1", "o0", "P2", "i0", Signal.XLR, "primary")]  # 仅有主用链路
    proj = Project(instances=[main, bak], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "REDUNDANT_NO_FAILOVER" in codes, codes


def test_redundant_pair_with_failover_silent():
    """画了 role="backup" 连线 → 不误报。"""
    main = _audio_dev("P1", "PROCESSOR", out_signals=(Signal.XLR,),
                      in_signals=(Signal.XLR,),
                      redundancy=Redundancy.PROCESSOR_BACKUP, pair="P2")
    bak = _audio_dev("P2", "PROCESSOR", out_signals=(Signal.XLR,),
                     in_signals=(Signal.XLR,),
                     redundancy=Redundancy.PROCESSOR_BACKUP, is_backup=True, pair="P1")
    conns = [
        Connection("P1", "o0", "P2", "i0", Signal.XLR, "primary"),
        Connection("P1", "o0", "P2", "i0", Signal.XLR, "backup"),  # 有 failover 线
    ]
    proj = Project(instances=[main, bak], connections=conns)
    assert "REDUNDANT_NO_FAILOVER" not in [i.code for i in validate(proj)]


def test_link_backup_without_failover_line_silent():
    """LINK_BACKUP（failover_link=False）即便无 backup 线也不报（冗余在网络层）。"""
    main = _audio_dev("D1", "PROCESSOR", out_signals=(Signal.DANTE,),
                      in_signals=(Signal.DANTE,),
                      redundancy=Redundancy.LINK_BACKUP, pair="D2")
    bak = _audio_dev("D2", "PROCESSOR", out_signals=(Signal.DANTE,),
                     in_signals=(Signal.DANTE,),
                     redundancy=Redundancy.LINK_BACKUP, is_backup=True, pair="D1")
    conns = [Connection("D1", "o0", "D2", "i0", Signal.DANTE, "primary")]
    proj = Project(instances=[main, bak], connections=conns)
    assert "REDUNDANT_NO_FAILOVER" not in [i.code for i in validate(proj)]


# ============================================================ S10：dual_switch 精准判定
def test_spoF_no_dual_switch_fires_for_dual_switch_redundancy():
    """LINK/FULL_CHAIN（dual_switch=True）却仅 1 台交换机 → 仍须报。"""
    sw = _audio_dev("SW1", "SWITCH", out_signals=(Signal.XLR,),
                    in_signals=(Signal.XLR,))
    main = _audio_dev("D1", "PROCESSOR", out_signals=(Signal.XLR,),
                      in_signals=(Signal.XLR,),
                      redundancy=Redundancy.LINK_BACKUP, pair="D2")
    bak = _audio_dev("D2", "PROCESSOR", out_signals=(Signal.XLR,),
                     in_signals=(Signal.XLR,),
                     redundancy=Redundancy.LINK_BACKUP, is_backup=True, pair="D1")
    conns = [
        Connection("D1", "o0", "SW1", "i0", Signal.XLR, "primary"),
        Connection("D2", "o0", "SW1", "i0", Signal.XLR, "primary"),
    ]
    proj = Project(instances=[main, bak, sw], switches=[sw], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "SPOF_NET_NO_DUAL_SWITCH" in codes, codes


def test_spoF_no_dual_switch_no_false_positive_for_processor_backup():
    """PROCESSOR_BACKUP（dual_switch=False）即便只 1 台交换机也不误报。"""
    sw = _audio_dev("SW1", "SWITCH", out_signals=(Signal.XLR,),
                    in_signals=(Signal.XLR,))
    main = _audio_dev("P1", "PROCESSOR", out_signals=(Signal.XLR,),
                      in_signals=(Signal.XLR,),
                      redundancy=Redundancy.PROCESSOR_BACKUP, pair="P2")
    bak = _audio_dev("P2", "PROCESSOR", out_signals=(Signal.XLR,),
                     in_signals=(Signal.XLR,),
                     redundancy=Redundancy.PROCESSOR_BACKUP, is_backup=True, pair="P1")
    conns = [
        Connection("P1", "o0", "SW1", "i0", Signal.XLR, "primary"),
        Connection("P2", "o0", "SW1", "i0", Signal.XLR, "primary"),
        Connection("P1", "o0", "P2", "i0", Signal.XLR, "backup"),  # 已画 failover 线
    ]
    proj = Project(instances=[main, bak, sw], switches=[sw], connections=conns)
    codes = [i.code for i in validate(proj)]
    assert "SPOF_NET_NO_DUAL_SWITCH" not in codes, codes


# ============================================================ S7：只读目录下 save 不崩
def test_save_does_not_raise_on_readonly_dir(tmp_path):
    """★ S7：图例库存放目录只读时，save() 必须安全跳过而非抛 PermissionError。"""
    ro = tmp_path / "ro"
    ro.mkdir()
    lib = ro / "legend_library.json"
    lib.write_text('{"legends":[]}', encoding="utf-8")
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)  # 0o500：只读
    try:
        st = LegendStore(path=str(lib))
        st.put(Legend(brand="IPS", model="X1", category="IO",
                      ports=[LegendPort(signal="XLR", role="in", count=1)]))
        # 不应抛异常（只读目录：mkdir/write 被捕获后安全返回）
        st.save()
        # 内存记录仍在，会话内可用
        assert st.has("IPS", "X1", "IO"), "只读短路后内存记录应保留"
    finally:
        os.chmod(ro, stat.S_IRWXU)  # 还原以便 pytest 清理


def test_resolve_legend_path_redirects_when_default_readonly(monkeypatch, tmp_path):
    """★ S7：默认库所在目录只读时，_resolve_legend_path 应重定向到用户可写目录。"""
    ro = tmp_path / "bundled_data"
    ro.mkdir()
    ro_file = ro / "legend_library.json"
    ro_file.write_text('{"legends":[]}', encoding="utf-8")
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)  # 0o500：只读
    try:
        monkeypatch.setattr(legend_store, "DEFAULT_CACHE", ro_file)
        resolved = _resolve_legend_path()
        assert resolved != ro_file, "默认库只读时应重定向"
        assert os.access(str(resolved.parent), os.W_OK), \
            f"重定向目标须可写：{resolved}"
    finally:
        os.chmod(ro, stat.S_IRWXU)
