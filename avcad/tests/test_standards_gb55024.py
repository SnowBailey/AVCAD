"""GB 55024-2022 全文强制项 → checklist 映射（零新字段，纯数据驱动）。"""
from avcad.model.schema import DeviceInstance, Port, Signal, Project
from avcad.validate.standards_gb55024 import check_standards, STANDARDS_REGISTRY
from avcad.validate.checks import validate
from avcad.core.build import build_project
from avcad.workflow.legend_store import LegendStore, Legend


def _audio_dev(uid, cat="MIXER", signals=("XLR",), features=None, electrical=None):
    ports = [Port(id=f"{uid}_o", side="right", signal=getattr(Signal, s), label=s)
             for s in signals]
    return DeviceInstance(uid=uid, category=cat, name=uid,
                          ports=ports, features=features or set(),
                          electrical=electrical or {})


def test_registry_has_three_rules():
    rules = {r["rule"] for r in STANDARDS_REGISTRY}
    assert {"DESIGN_BASIS", "GROUNDING", "EMG_BROADCAST"} <= rules


def test_electrical_device_triggers_design_basis_and_grounding():
    # 含 POWER 端口的交换机 → 应触发设计依据(INFO) + 接地(WARN)
    proj = Project(instances=[_audio_dev("sw", "SWITCH", signals=("POWER",))])
    codes = {i.code for i in check_standards(proj)}
    assert "GB55024_DESIGN_BASIS" in codes
    assert "GB55024_GROUNDING" in codes


def test_non_electrical_device_silent():
    # 纯音频设备（XLR 出） → 不触发任何强条提醒
    proj = Project(instances=[_audio_dev("m", "MIXER", signals=("XLR",))])
    assert check_standards(proj) == []


def test_emergency_broadcast_triggers_emg_rule():
    # features 含 EMG → 触发消防应急广播强条双锚(WARN)
    proj = Project(instances=[_audio_dev("spk", "SPEAKER", signals=("XLR",),
                                         features={"EMG"})])
    codes = {i.code for i in check_standards(proj)}
    assert "GB55024_EMG_BROADCAST" in codes
    # 无电气设备 → 不应误报接地
    assert "GB55024_GROUNDING" not in codes


def test_validate_includes_standards_reminders():
    # validate() 应把强条提醒并入 issues（零新字段，靠现有数据触发）
    proj = Project(instances=[_audio_dev("sw", "SWITCH", signals=("POWER",))])
    issues = validate(proj)
    codes = {i.code for i in issues}
    assert "GB55024_DESIGN_BASIS" in codes
    assert "GB55024_GROUNDING" in codes


def test_grounding_wire_below_min_raises_error():
    # 填了线径且 <4mm² → ERROR，且不再发 WARN 接地提醒
    proj = Project(instances=[_audio_dev("sw", "SWITCH", signals=("POWER",),
                                         electrical={"ground_wire_mm2": 2.5})])
    issues = check_standards(proj)
    codes = {i.code for i in issues}
    assert "GB55024_GROUNDING_WIRE" in codes
    assert "GB55024_GROUNDING" not in codes
    err = [i for i in issues if i.code == "GB55024_GROUNDING_WIRE"][0]
    assert err.level == "ERROR"


def test_grounding_wire_ok_silent():
    # 填了线径且 ≥4mm² → 不报接地问题（仅保留设计依据 INFO）
    proj = Project(instances=[_audio_dev("sw", "SWITCH", signals=("POWER",),
                                         electrical={"ground_wire_mm2": 6.0})])
    codes = {i.code for i in check_standards(proj)}
    assert "GB55024_GROUNDING_WIRE" not in codes
    assert "GB55024_GROUNDING" not in codes
    assert "GB55024_DESIGN_BASIS" in codes


def test_emg_spl_below_floor_raises_error():
    # 应急广播声压级 <60dB → ERROR，且不再发 WARN 强条双锚
    proj = Project(instances=[_audio_dev("spk", "SPEAKER", signals=("XLR",),
                                         features={"EMG"},
                                         electrical={"emg_spl_db": 55.0})])
    issues = check_standards(proj)
    codes = {i.code for i in issues}
    assert "GB55024_EMG_SPL" in codes
    assert "GB55024_EMG_BROADCAST" not in codes
    assert [i for i in issues if i.code == "GB55024_EMG_SPL"][0].level == "ERROR"


def test_emg_spl_margin_via_meta_raises_error():
    # meta 取数：背景 55dB → 要求 ≥70dB，设计 62dB 不足 → ERROR
    proj = Project(instances=[_audio_dev("spk", "SPEAKER", signals=("XLR",),
                                         features={"EMG"})],
                   meta={"emergency_broadcast_spl_db": 62.0,
                         "background_spl_db": 55.0})
    issues = check_standards(proj)
    codes = {i.code for i in issues}
    assert "GB55024_EMG_SPL" in codes
    assert "GB55024_EMG_BROADCAST" not in codes


def test_emg_spl_ok_silent():
    # 声压级达标（≥ max(60, 背景+15)）→ 不报任何应急广播问题
    proj = Project(instances=[_audio_dev("spk", "SPEAKER", signals=("XLR",),
                                         features={"EMG"},
                                         electrical={"emg_spl_db": 72.0,
                                                     "bg_spl_db": 50.0})])
    codes = {i.code for i in check_standards(proj)}
    assert "GB55024_EMG_SPL" not in codes
    assert "GB55024_EMG_BROADCAST" not in codes


def test_legend_electrical_flows_to_instance_and_errors():
    # 图例校正页填的 electrical → LegendStore.apply 覆盖实例 electrical → 触发 ERROR
    inst = _audio_dev("sw", "SWITCH", signals=("POWER",))
    proj = Project(instances=[inst])
    lg = Legend(brand="X", model="SW1", category="SWITCH",
                electrical={"ground_wire_mm2": 2.5})
    LegendStore().apply(inst, lg)
    assert inst.electrical.get("ground_wire_mm2") == 2.5
    codes = {i.code for i in check_standards(proj)}
    assert "GB55024_GROUNDING_WIRE" in codes


def test_entry_electrical_flows_via_build_and_errors():
    # 导入清单「电气」列 → build_project 把 entry.electrical 并进实例 → 触发 ERROR
    entries = [{
        "category": "SWITCH", "brand": "X", "model": "SW1", "quantity": 1,
        "electrical": {"ground_wire_mm2": 2.5},
    }]
    proj = build_project(entries, "t")
    assert any((i.electrical or {}).get("ground_wire_mm2") == 2.5 for i in proj.instances)
    codes = {i.code for i in validate(proj)}
    assert "GB55024_GROUNDING_WIRE" in codes
