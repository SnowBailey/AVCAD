"""M2 模块确认 / 排除管线测试。"""
from avcad.workflow.module_confirm import (
    build_module_list, confirm_modules, ModuleItem,
)


def _entries():
    return [
        {"category": "SOURCE", "brand": "SHURE", "model": "SM58", "name": "话筒", "quantity": 4},
        {"category": "SOURCE", "brand": "SHURE", "model": "SM58", "name": "话筒", "quantity": 2},
        {"category": "MIXER", "brand": "YAMAHA", "model": "QL5", "name": "调音台", "quantity": 1},
        {"category": "AMP", "brand": "", "model": "XM", "name": "功放", "quantity": 2},
    ]


def test_build_module_list_groups_and_sums():
    items = build_module_list(_entries())
    # 同 brand+model(SM58) 应合并为 1 个模块，数量 6
    sm = [i for i in items if i.model == "SM58"]
    assert len(sm) == 1
    assert sm[0].quantity == 6
    assert len(sm[0].lines) == 2
    # 共 3 个模块
    assert len(items) == 3


def test_confirm_all_included_unchanged():
    entries = _entries()
    filtered, excluded = confirm_modules(entries, {})
    assert len(filtered) == len(entries)
    assert excluded == []


def test_confirm_exclude_by_model():
    entries = _entries()
    filtered, excluded = confirm_modules(entries, {"SM58": "exclude"})
    # SM58 两行都应被剔除
    assert all(e.get("model") != "SM58" for e in filtered)
    assert len(filtered) == 2
    # 排除记录保留
    assert len(excluded) == 1
    assert excluded[0].model == "SM58"
    assert excluded[0].quantity == 6


def test_confirm_exclude_by_full_key():
    entries = _entries()
    # 仅排除 SHURE::SM58（若同 model 不同 brand 可区分）
    filtered, excluded = confirm_modules(entries, {"SHURE::SM58": "exclude"})
    assert all(not (e.get("brand") == "SHURE" and e.get("model") == "SM58") for e in filtered)
    assert len(excluded) == 1


def test_confirm_unknown_decision_key_ignored():
    entries = _entries()
    filtered, excluded = confirm_modules(entries, {"NOPE": "exclude"})
    assert len(filtered) == len(entries)
    assert excluded == []
