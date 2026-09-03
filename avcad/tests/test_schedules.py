"""D14 线缆清册 / D03 端子表 投影模块测试。"""
from __future__ import annotations

import os
import sys

import pytest

from avcad.core.build import build_project
from avcad.model.schema import Project, DeviceInstance, ConcretePort, Connection, Signal

# 确保 repo root 在 sys.path（从 avcad/tests 直接 `python -m pytest` 也能跑）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from avcad.deliverables.schedules import (
    cable_schedule,
    terminal_schedule,
    terminal_rows,
)


# 清单条目（与任务描述一致）：SOURCE / MIXER / AMP / SPEAKER
_ENTRIES = [
    {"category": "SOURCE", "name": "MT110", "features": ["analog"],
     "params": {"outputs": 1}},
    {"category": "MIXER", "name": "QU-16", "features": ["analog"],
     "params": {"inputs": 16, "outputs": 8}},
    {"category": "AMP", "name": "DA250Q", "features": ["analog", "control"],
     "params": {"channels": 4}},
    {"category": "SPEAKER", "name": "CI600", "quantity": 2,
     "params": {"impedance_ohm": 8, "power_w": 80}},
]


@pytest.fixture
def proj():
    return build_project(_ENTRIES, name="test")


# Test 1：线缆清册至少 1 行，且每行关键字段非空
def test_cable_schedule_nonempty(proj):
    rows = cable_schedule(proj)
    assert len(rows) >= 1
    for r in rows:
        assert r["from_device"]
        assert r["to_device"]
        assert r["signal"]


# Test 2：每个清单设备的 uid 都在端子表里，且端口非空
def test_terminal_schedule_covers_instances(proj):
    sch = terminal_schedule(proj)
    for i in proj.instances:
        assert i.uid in sch
        assert sch[i.uid]["ports"]


# Test 3：terminal_rows 行数 == 全部 ConcretePort 数（清单设备 + 交换机）
def test_terminal_rows_count(proj):
    rows = terminal_rows(proj)
    total_ports = sum(len(i.ports) for i in (proj.instances + proj.switches))
    assert len(rows) == total_ports


# Test 4：模块可导入
def test_import_schedules():
    import avcad.deliverables.schedules  # noqa: F401
