"""富集层导入测试：类别推断 / 吊架排除 / 功放参数归一化 / 真实 xlsx 端到端。"""
from __future__ import annotations
import os

from avcad.workflow.importers import (
    classify_category, is_rigging, extract_params, extract_features,
    build_entries, to_bom_csv,
)

REAL_XLSX = "/Users/mac/Desktop/测试.xlsx"


def test_classify_category_basic():
    assert classify_category("数字功率放大器") == "AMP"
    assert classify_category("数字调音台") == "MIXER"
    assert classify_category("数字音频处理器（带网络监测及dante）") == "PROCESSOR"
    assert classify_category("16mm大振膜方杆模拟话筒") == "SOURCE"
    assert classify_category("左右全频线阵列扬声器") == "SPEAKER"


def test_is_rigging_excluded():
    assert is_rigging("线阵列扬声器吊架")
    assert is_rigging("ML210 飞行架")
    assert not is_rigging("左右全频线阵列扬声器")
    assert classify_category("线阵列扬声器吊架") is None


def test_extract_params_amp():
    spec = "1、8Ω 2通道≥：900W \n2、信噪比≥108dB"
    p = extract_params(spec, "AMP")
    assert p["channels"] == 2
    assert p["power_w_per_ch"] == 900


def test_extract_features_dante_control():
    feats = extract_features("具备Dante冗余网口；支持集控系统控制", "数字音频处理器", "PROCESSOR")
    assert "dante" in feats
    assert "control" in feats
    # analog 仅在规格含「模拟」时抽取
    feats2 = extract_features("具备Dante；模拟输入12路/模拟输出8路", "数字音频处理器", "PROCESSOR")
    assert "analog" in feats2


def test_to_bom_csv_roundtrip_electrical():
    entries = [{
        "category": "AMP", "brand": "ezacoustics", "model": "EM30D",
        "name": "数字功率放大器", "quantity": 5,
        "features": ["analog", "control"], "params": {"channels": 2},
        "electrical": {"min_load_ohm": 4, "power_w_per_ch": 900},
    }]
    csv_text = to_bom_csv(entries)
    assert "电气" in csv_text
    # 解析回来电气应保留
    from avcad.parse.bom_parser import parse_bom
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(csv_text); tmp = f.name
    try:
        parsed = parse_bom(tmp)
        assert parsed[0]["electrical"]["power_w_per_ch"] == 900
        assert parsed[0]["electrical"]["min_load_ohm"] == 4
        assert parsed[0]["params"]["channels"] == 2
    finally:
        os.unlink(tmp)


def test_build_entries_real_xlsx():
    if not os.path.exists(REAL_XLSX):
        import pytest
        pytest.skip("真实清单 测试.xlsx 不在本机")
    entries, dropped = build_entries(REAL_XLSX)
    # 吊架被排除
    dropped_names = [str(d.get("设备名称") or d.get("名称")) for d in dropped]
    assert any("吊架" in n for n in dropped_names)
    by_model = {e["model"]: e for e in entries}
    # QU-16 主库 deferred -> 兜底 MIXER
    assert by_model["QU-16"]["category"] == "MIXER"
    # 功放归一化
    assert by_model["EM30D"]["params"].get("channels") == 2
    assert by_model["EM30D"]["electrical"]["power_w_per_ch"] == 900
    assert by_model["EM50Q"]["params"].get("channels") == 4
    # 扬声器数量合计
    spk = sum(e["quantity"] for e in entries if e["category"] == "SPEAKER")
    assert spk == 28
    # ML210FB 不在条目内
    assert "ML210FB" not in by_model
