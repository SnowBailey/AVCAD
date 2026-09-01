"""M1 图例持久化缓存层测试。"""
import os
import tempfile
from avcad.model.schema import DeviceInstance, Signal, ConcretePort
from avcad.workflow.legend_store import LegendStore, Legend, LegendPort


def _mk_inst(uid="dev1", brand="YAMAHA", model="RIO3224-D"):
    return DeviceInstance(uid=uid, category="IO", name="舞台接口箱",
                          brand=brand, model=model, ports=[])


def _mk_legend(brand="YAMAHA", model="RIO3224-D"):
    return Legend(
        brand=brand, model=model, category="IO",
        ports=[
            LegendPort(signal="XLR", role="in", side="left", count=1, label="IN"),
            LegendPort(signal="XLR", role="out", side="right", count=1, label="OUT"),
            LegendPort(signal="DANTE", role="in", side="right", count=1, label="DANTE"),
            LegendPort(signal="IP", role="in", side="right", count=1, label="NET"),
        ],
        slots=[{"type": "HY", "count": 4, "label": "HY"}],
    )


def test_put_get_memory():
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_nofile.json"))
    lg = _mk_legend()
    st.put(lg)
    assert st.has("YAMAHA", "RIO3224-D")
    got = st.get("YAMAHA", "RIO3224-D")
    assert got is not None
    assert len(got.ports) == 4
    assert got.slots[0]["type"] == "HY"


def test_missing_returns_none():
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_nofile2.json"))
    assert st.get("FOO", "BAR") is None
    assert not st.has("FOO", "BAR")


def test_key_generic_namespace():
    assert LegendStore.key("", "") == "_generic::_"
    assert LegendStore.key("  ", "X1") == "_generic::X1"
    assert LegendStore.key("YAMAHA", "R1") == "YAMAHA::R1"


def test_save_load_file_roundtrip():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        st = LegendStore(path=p)
        st.put(_mk_legend("YAMAHA", "RIO3224-D"))
        st.put(_mk_legend("SHURE", "ULXD4Q"))
        st.save()
        # 重新加载
        st2 = LegendStore(path=p)
        assert st2.has("YAMAHA", "RIO3224-D")
        assert st2.has("SHURE", "ULXD4Q")
        g = st2.get("SHURE", "ULXD4Q")
        assert g.ports[0].signal == "XLR"
        assert g.category == "IO"
    finally:
        if os.path.exists(p):
            os.remove(p)


def test_apply_expands_ports_and_slots():
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_nofile3.json"))
    lg = _mk_legend()
    st.put(lg)
    inst = _mk_inst()
    st.apply(inst)
    # 4 个图例端口（均 count=1）应展开为 4 个 ConcretePort
    assert len(inst.ports) == 4
    by_label = {p.label: p for p in inst.ports}
    assert by_label["IN"].side == "left"
    assert by_label["OUT"].side == "right"
    assert by_label["DANTE"].signal == Signal.DANTE
    assert by_label["NET"].signal == Signal.IP
    assert inst.slots[0]["type"] == "HY"


def test_apply_respects_count():
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_nofile4.json"))
    lg = Legend(brand="X", model="M", category="AMP",
                ports=[LegendPort(signal="SPEAKER", role="out", side="right",
                                  count=4, label="OUT")])
    st.put(lg)
    inst = _mk_inst(brand="X", model="M")
    st.apply(inst)
    # count=4 → 4 个端口
    assert len(inst.ports) == 4
    assert all(p.label.startswith("OUT") for p in inst.ports)
    assert inst.ports[0].label == "OUT1" and inst.ports[3].label == "OUT4"


def test_apply_no_hit_leaves_instance_untouched():
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_nofile5.json"))
    inst = _mk_inst()
    inst.ports = [ConcretePort(id="x", uid="dev1", side="left", signal=Signal.XLR,
                               label="KEEP", index=0)]
    st.apply(inst)  # 缓存无命中
    assert len(inst.ports) == 1
    assert inst.ports[0].label == "KEEP"


# ---------------- 类别回退规则（2026-09-01 收紧） ----------------
# 判据：同型号跨类别设备（会议主机 vs 处理器）不能互相串号；
# 但单一类别的型号在主库类别漂移时不能整体失效。


def test_single_legend_cross_category_fallback_kept():
    """同型号只有一条图例 -> 允许跨类别命中（主库类别漂移时图例仍生效）。"""
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_cat1.json"))
    st.put(_mk_legend())                       # YAMAHA::RIO3224-D::IO
    got = st.get("YAMAHA", "RIO3224-D", "PROCESSOR")
    assert got is not None and got.category == "IO"


def test_multi_legend_requires_exact_category():
    """同型号有多条图例 -> 必须类别精确匹配，防止跨类别串号。"""
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_cat2.json"))
    st.put(_mk_legend())                       # ::IO
    host = _mk_legend()
    host.category = "MIC_HOST"
    host.ports = [LegendPort(signal="CONF", role="out", side="right", count=1,
                             label="MIX")]
    st.put(host)                               # ::MIC_HOST
    assert st.get("YAMAHA", "RIO3224-D", "MIC_HOST").category == "MIC_HOST"
    assert st.get("YAMAHA", "RIO3224-D", "IO").category == "IO"
    # 第四种类别 -> 不再静默回退到任意一条
    assert st.get("YAMAHA", "RIO3224-D", "PROCESSOR") is None
    assert not st.has("YAMAHA", "RIO3224-D", "PROCESSOR")


def test_apply_does_not_rewrite_category_on_multi_legend_miss():
    """跨类别未命中时，实例的 category 不能被别的类别图例改掉。"""
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_cat3.json"))
    st.put(_mk_legend())                       # ::IO
    host = _mk_legend()
    host.category = "MIC_HOST"
    st.put(host)
    inst = _mk_inst()                          # category=IO
    inst.category = "PROCESSOR"
    st.apply(inst)
    assert inst.category == "PROCESSOR"        # 未被 IO / MIC_HOST 顶掉


def test_no_category_query_still_matches_any():
    """未传 category 的旧调用方式：保持「取一条」的原行为。"""
    st = LegendStore(path=os.path.join(tempfile.gettempdir(), "lg_test_cat4.json"))
    st.put(_mk_legend())
    assert st.get("YAMAHA", "RIO3224-D") is not None
