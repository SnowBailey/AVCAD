"""设备类别语义知识库的回归守卫。

背景（2026-09-01）：阳哥痛点——拿一个「型号名不含类别词」的真实新型号，
旧识别逻辑会失效。新加载器 `category_kb.identify()` 用三级回退
（主库精确 → 型号片段 KNOWN_MODELS → 类别词模糊匹配）解决。

本文件把「这 12 个真实型号必须识别对」+「未知型号必须返回 None」变成硬断言，
以后改 device_kb.yaml / KNOWN_MODELS / 主库时，识别一旦退化立刻测试失败，
不用等用户拿到一张画错的图。
"""
from __future__ import annotations

import pytest

from avcad.model import category_kb


# 真实型号 → 期望类别（既覆盖主库命中，也覆盖仅靠型号片段命中的情况）
KNOWN_CASES = [
    # 主库精确命中
    ("IPS", "UM2000ASD", "ANT_COMBINE"),
    ("IPS", "CF6300", "MIC_HOST"),
    ("IPS", "CF6300WB", "ANTENNA"),
    ("IPS", "UM2000ATD", "ANT_DIST"),
    ("EZACOUSTICS", "RDD12", "IO"),
    ("Yamaha", "TF5", "MIXER"),
    ("ALLEN & HEATH", "QU16", "MIXER"),
    ("YAMAHA", "RIO3224-D", "IO"),
    # 仅靠型号片段命中（型号不在主库，验证 KNOWN_MODELS 层）
    ("SomeBrand", "RIO9999", "IO"),        # 片段 rio
    ("BrandX", "TF99", "MIXER"),           # 片段 tf
    ("Generic", "ULXD4Q", "WIRELESS_RX"),  # 片段 ulxd
    ("MyBrand", "CDJ-2025", "SOURCE"),     # 片段 cdj
    # 类别词模糊匹配（型号不含片段，但描述带别名）
    ("", "神秘设备 调音台 现场控制台", "MIXER"),
    ("", "天线分配器 射频分路", "ANT_DIST"),
    # 扩充后的品牌系列片段命中（守护 KNOWN_MODELS 覆盖）
    ("Yamaha", "CL5", "MIXER"),
    ("L-Acoustics", "KARA", "SPEAKER"),
    ("QSC", "Core 110f", "PROCESSOR"),
    ("Powersoft", "X4", "AMP"),
    ("Sennheiser", "ew300", "WIRELESS_RX"),
    ("Audio-Technica", "ATW-3000", "WIRELESS_RX"),
    ("Shure", "UA844SWB", "ANT_DIST"),
    ("dbx", "DriveRack PA2", "SPEAKER_MGR"),
]


@pytest.mark.parametrize("brand,model,expect", KNOWN_CASES)
def test_identify_known_models(brand, model, expect):
    r = category_kb.identify(brand, model)
    assert r["category"] == expect, f"{brand} {model} 应识别为 {expect}，实为 {r['category']}"
    assert r["confidence"] > 0
    assert r["cn"]  # 中文名非空


def test_identify_unknown_returns_none():
    # 完全不含任何片段/类别词的型号 → 必须返回 None 提示人工指定
    r = category_kb.identify("FOO", "BAR-99")
    assert r["category"] is None
    assert r["confidence"] == 0.0


def test_all_categories_have_required_semantic_fields():
    required = {"cn", "role", "upstream", "downstream", "typical_ports", "identification"}
    for cat in category_kb.list_categories():
        kb = category_kb.get_kb(cat)
        missing = required - kb.keys()
        assert not missing, f"类别 {cat} 缺语义字段 {missing}"
        assert kb["cn"], f"类别 {cat} 中文名不能为空"
        assert isinstance(kb["typical_ports"], list) and kb["typical_ports"], \
            f"类别 {cat} 的 typical_ports 应为非空列表"


def test_expected_category_set_complete():
    # 14 类必须齐全（新增类别时同步更新这里 + device_specs + draw 映射表）
    expected = {
        "SOURCE", "WIRELESS_MIC", "WIRELESS_RX", "ANTENNA", "ANT_DIST",
        "ANT_COMBINE", "MIXER", "PROCESSOR", "SPEAKER_MGR", "MIC_HOST",
        "AMP", "SPEAKER", "IO", "SWITCH",
    }
    assert set(category_kb.list_categories()) == expected


def test_usage_hint_known_model():
    h = category_kb.usage_hint("IPS", "CF6300")
    assert h["identified"] is True
    assert h["cn"] == "话筒主机"
    assert h["category"] == "MIC_HOST"
    assert isinstance(h["upstream"], list) and h["upstream"]
    assert isinstance(h["downstream"], list) and h["downstream"]


def test_usage_hint_unknown_model():
    h = category_kb.usage_hint("FOO", "BAR-99")
    assert h["identified"] is False
    assert h["category"] is None
    assert h["confidence"] == 0.0


def test_module_item_carries_kb_hint():
    # 第②步模块序列化必须带 kb_hint，前端才能展示识别建议
    from avcad.ui.app import _module_item  # 间接验证后端接线
    from types import SimpleNamespace

    m = SimpleNamespace(brand="IPS", model="CF6300", category="MIC_HOST",
                        name="会议主机", quantity=1, decision="include",
                        source="catalog", features=set(), params={})
    item = _module_item(m)
    assert "kb_hint" in item
    assert item["kb_hint"]["identified"] is True
    assert item["kb_hint"]["cn"] == "话筒主机"


def test_normalize_cat_maps_stage_keys():
    # 内部 stage 键必须归一化到知识库类别码，否则 PROC_PRE→PROCESSOR 等
    # 配对会被 is_valid_link 误判越界（R19）。
    assert category_kb.normalize_cat("PROC_PRE") == "PROCESSOR"
    assert category_kb.normalize_cat("PROC_POST") == "PROCESSOR"
    assert category_kb.normalize_cat("SIDE") == "IO"
    assert category_kb.normalize_cat("MIXER") == "MIXER"   # 未知原样返回


def test_is_valid_link_known_pairs():
    # ★ R19 接语义：router.py 的通用配对以 is_valid_link 为权威闸门。
    #   这些「应当能接」的类别对必须返回 valid=True（含 stage 归一化情形）。
    valid_pairs = [
        ("SOURCE", "MIXER"),
        ("SOURCE", "PROC_PRE"),        # PROC_PRE 归一为 PROCESSOR，SOURCE.downstream 含 PROCESSOR
        ("PROC_PRE", "MIXER"),         # PROCESSOR 在 MIXER.upstream
        ("PROC_POST", "SPEAKER_MGR"),  # PROCESSOR.downstream 含 SPEAKER_MGR
        ("MIC_HOST", "MIXER"),         # MIC_HOST.downstream 含 MIXER
        ("MIC_HOST", "PROC_PRE"),      # MIC_HOST.downstream 含 PROCESSOR
        ("SPEAKER_MGR", "AMP"),
        ("AMP", "SPEAKER"),
        ("WIRELESS_RX", "MIXER"),
        ("WIRELESS_RX", "PROC_PRE"),
        ("ANTENNA", "ANT_COMBINE"),    # ANTENNA.upstream 含 ANT_COMBINE
        ("SWITCH", "SPEAKER"),         # SWITCH.downstream 含 SPEAKER（Dante 供电箱）
    ]
    for a, b in valid_pairs:
        ok, reason = category_kb.is_valid_link(a, b)
        assert ok, f"{a}→{b} 应为合法链路，却判越界：{reason}"


def test_is_valid_link_invalid_pairs():
    # 这些「不该直连」的类别对必须返回 valid=False， Gates 才会跳过并告警。
    invalid_pairs = [
        ("PROCESSOR", "MIC_HOST"),   # 处理器不反向喂会议主机
        ("SPEAKER", "AMP"),          # 扬声器是终点，不反向喂功放
        ("MIXER", "SOURCE"),         # 调音台不反向喂音源
    ]
    for a, b in invalid_pairs:
        ok, reason = category_kb.is_valid_link(a, b)
        assert not ok, f"{a}→{b} 应为越界链路，却判合法：{reason}"
        assert "不在 KB 上下游" in reason

