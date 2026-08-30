"""M5 端到端工作流管线集成测试。"""
import os
import tempfile
from avcad.workflow.run import run_workflow, summarize
from avcad.workflow.legend_store import LegendStore, Legend, LegendPort

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES = os.path.join(ROOT, "deliverables", "system_samples")


def _text(name):
    return open(os.path.join(SAMPLES, name), encoding="utf-8-sig").read()


def _store():
    # 隔离的临时缓存，避免污染默认 legend_cache.json
    return LegendStore(path=tempfile.mktemp(suffix=".json"))


def test_run_basic_conference():
    r = run_workflow(_text("bom_A_conference.csv"), legend_store=_store())
    proj = r["project"]
    assert len(proj.instances) > 0
    # 该 BOM 实际为「会议+无线」混合体，主分类在 A_conference / J_multifunc 间合理
    assert r["architecture"][0].id in ("A_conference", "J_multifunc")
    # 全新缓存 → 所有型号均为 cache_miss
    assert len(r["cache_miss"]) > 0
    # A 场景应无 ERROR 级问题
    errs = [i for i in proj.issues if i.level == "ERROR"]
    assert errs == [], [f"{i.code}:{i.msg}" for i in errs]
    # 含 Dante 设备 → 至少 1 台交换机
    assert len(proj.switches) >= 1
    assert "工程" in summarize(r)


def test_run_exclude_module():
    r = run_workflow(_text("bom_A_conference.csv"), decisions={"ULXD4D": "exclude"},
                     legend_store=_store())
    cats = {i.category for i in r["project"].instances}
    assert "WIRELESS_RX" not in cats
    assert any(x.model == "ULXD4D" for x in r["excluded"])


def test_run_full_chain_two_switches():
    # 以基础会议清单 + 全链路主备参数，验证生成 2 台交换机
    r = run_workflow(_text("bom_A_conference.csv"), redundancy="FULL_CHAIN",
                     legend_store=_store())
    assert len(r["project"].switches) == 2


def test_run_respects_entries_redundancy():
    # bom_E 自带 FULL_CHAIN 冗余且未传 redundancy 参数 → 应尊重并生成 2 台交换机
    r = run_workflow(_text("bom_E_redundancy.csv"), legend_store=_store())
    assert len(r["project"].switches) == 2


def test_run_cache_hit_no_miss():
    st = _store()
    r1 = run_workflow(_text("bom_A_conference.csv"), legend_store=st)
    # 为每个未命中型号预置一个图例（模拟用户已确认）
    for k in r1["cache_miss"]:
        b, m = k.split("::", 1)
        st.put(Legend(brand=b, model=m, category="SOURCE",
                      ports=[LegendPort(signal="XLR", role="in", side="left",
                                         count=1, label="IN")]))
    st.save()
    r2 = run_workflow(_text("bom_A_conference.csv"), legend_store=st)
    assert r2["cache_miss"] == []


def test_run_all_ten_samples_no_error():
    for s in ["A_conference", "B_wireless", "C_foh", "D_distributed", "E_redundancy",
              "F_theatre", "G_studio", "H_pa", "I_touring", "J_multifunc"]:
        r = run_workflow(_text(f"bom_{s}.csv"), legend_store=_store())
        errs = [i for i in r["project"].issues if i.level == "ERROR"]
        assert errs == [], f"{s}: " + str([f"{i.code}:{i.msg}" for i in errs])
