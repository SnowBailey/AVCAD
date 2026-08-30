"""M4 参考架构模板库 + 最优选择器测试。"""
from avcad.workflow.architecture import TEMPLATES, select, recommended, profile_of


def _bom_A():
    return [
        {"category": "SOURCE", "model": "SM58", "quantity": 4},
        {"category": "PROCESSOR", "model": "DSP", "quantity": 1},
        {"category": "SPEAKER", "model": "SP", "quantity": 2},
    ]


def _bom_E():
    return [
        {"category": "PROCESSOR", "model": "P", "quantity": 1},
        {"category": "MIXER", "model": "M", "quantity": 2},
    ]


def _bom_I():
    return [
        {"category": "WIRELESS_MIC", "model": "W", "quantity": 16},
        {"category": "ANT_DIST", "model": "AD", "quantity": 1},
        {"category": "WIRELESS_RX", "model": "RX", "quantity": 1},
        {"category": "MIXER", "model": "M", "quantity": 2},
        {"category": "PROCESSOR", "model": "P", "quantity": 1},
        {"category": "SPEAKER", "model": "S", "quantity": 4},
    ]


def test_ten_templates_present():
    ids = [t.id for t in TEMPLATES]
    assert len(ids) == 10
    assert "A_conference" in ids and "I_touring" in ids


def test_conference_profile_picks_A_top():
    top = recommended(_bom_A())
    assert top[0].id == "A_conference"


def test_full_chain_redundancy_picks_E_or_I():
    top = recommended(_bom_E(), redundancy="FULL_CHAIN")
    assert top[0].id in ("E_redundancy", "I_touring")
    # E 明确要求冗余，且无 penalty
    assert any("主备" in n or "SPOF" in n for n in top[2])


def test_touring_profile_with_full_chain_top_is_I():
    top = recommended(_bom_I(), redundancy="FULL_CHAIN")
    assert top[0].id == "I_touring"
    # 含 SPOF 交换机建议
    assert any("交换机" in n for n in top[2])


def test_select_returns_sorted_list_with_scores():
    ranked = select(_bom_A())
    assert len(ranked) == 10
    scores = [s for _, s, _ in ranked]
    assert scores == sorted(scores, reverse=True)


def test_profile_of_extracts_categories():
    cats = profile_of(_bom_I())
    assert "WIRELESS_RX" in cats and "MIXER" in cats and "SPEAKER" in cats
