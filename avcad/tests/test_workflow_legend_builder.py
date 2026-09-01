"""M3 图例定义器测试。"""
import os
import tempfile
from avcad.model.specs import load_specs, expand_instance
from avcad.workflow.legend_store import LegendStore
from avcad.workflow import legend_builder as lb


def _io_inst():
    spec = load_specs()["IO"]
    return expand_instance(spec, {"name": "舞台接口箱", "brand": "YAMAHA",
                                  "model": "RIO3224-D", "features": "dante,aes,control"}, 0)


def _store():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return LegendStore(path=p)


def test_from_instance_groups_ports_with_count():
    inst = _io_inst()
    legend = lb.from_instance(inst)
    assert legend.brand == "YAMAHA" and legend.model == "RIO3224-D"
    by_label = {p.label: p for p in legend.ports}
    # io.yaml: IN(left) / OUT(right) / DANTE(right) / AES(right) / NET(right)
    assert by_label["IN"].side == "left"
    assert by_label["OUT"].side == "right"
    assert by_label["DANTE"].signal == "DANTE"
    assert by_label["NET"].signal == "IP"
    assert len(legend.ports) == 5


def test_ensure_caches_default_on_miss_and_hits_after():
    st = _store()
    inst = _io_inst()
    assert not st.has("YAMAHA", "RIO3224-D")
    lg1 = lb.ensure(inst, st)
    assert st.has("YAMAHA", "RIO3224-D")
    # 再次 ensure 应返回缓存（不再重建）
    lg2 = lb.ensure(inst, st)
    assert lg2 is lg1 or lg2.ports == lg1.ports
    # 持久化后新 store 仍能命中
    st.save()
    st2 = LegendStore(path=st.path)
    assert st2.has("YAMAHA", "RIO3224-D")


def test_replace_ports_overrides_definition():
    st = _store()
    inst = _io_inst()
    lg = lb.ensure(inst, st)
    # 用户重新定义：全部改为右侧、增加一路 NET
    lg = lb.replace_ports(lg, [
        {"signal": "XLR", "role": "in", "side": "left", "count": 1, "label": "IN"},
        {"signal": "XLR", "role": "out", "side": "right", "count": 1, "label": "OUT"},
        {"signal": "DANTE", "role": "in", "side": "right", "count": 1, "label": "DANTE"},
        {"signal": "IP", "role": "in", "side": "right", "count": 2, "label": "NET"},
    ])
    st.put(lg)
    st.save()
    reloaded = st.get("YAMAHA", "RIO3224-D")
    ctrl = [p for p in reloaded.ports if p.label == "NET"]
    assert len(ctrl) == 1 and ctrl[0].count == 2


def test_add_remove_slot():
    st = _store()
    inst = _io_inst()
    lg = lb.ensure(inst, st)
    lb.add_slot(lg, {"type": "HY", "count": 4, "label": "HY"})
    assert len(lg.slots) == 1
    lb.remove_slot(lg, 0)
    assert len(lg.slots) == 0
