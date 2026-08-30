"""无线真分集子系统测试：UM2002 接收机规格、UM2000ATD 级联、每 4 口 1 台接收机。

规则出处（阳哥 2026-08-30 确认 + 易科官网/主库 remark）：
- UM2002 系列：一拖二真分集，双通道 × 每通道 2 天线 = **4 个 BNC 天线口**，2 路 XLR 独立输出。
- UM2000ATD 十通道天线分配器：**2 进 / 10 出**；非末台取 2 个出口级联下一台，
  剩余出口每 4 个接 1 台接收机；末台全部出口可用。
"""
from __future__ import annotations

from avcad.core.build import build_project
from avcad.parse.product_resolver import resolve
from avcad.wires.router import required_dist_count


def _rx(uid_extra=0, **over):
    base = {
        "category": "WIRELESS_RX", "brand": "IPS", "model": "UM2002",
        "name": "无线接收机", "quantity": 1, "features": [],
        "params": {"channels": 2, "antennas": 4},
    }
    base.update(over)
    return base


def _dist(**over):
    base = {
        "category": "ANT_DIST", "brand": "IPS", "model": "UM2000ATD",
        "name": "天线分配器", "quantity": 1,
        "params": {"inputs": 2, "outputs": 10},
    }
    base.update(over)
    return base


def _mixer():
    return {"category": "MIXER", "brand": "IPS", "model": "M16",
            "name": "调音台", "quantity": 1, "params": {"inputs": 16, "outputs": 8}}


# ---------------- 主库解析 ----------------
def test_um2002_resolves_to_receiver_with_4_antennas():
    """UM2002 套装的核心是接收机，且真分集双通道占 4 个天线口。"""
    e = {"brand": "IPS", "model": "UM2002", "name": "无线双手持麦克风"}
    resolve(e)
    assert e["category"] == "WIRELESS_RX", f"实际 {e['category']}"
    assert e["params"]["antennas"] == 4
    assert e["params"]["channels"] == 2


def test_um2002_variants_resolve_same():
    for m in ("UM2002L", "UM2002H"):
        e = {"brand": "IPS", "model": m, "name": "无线麦克风"}
        resolve(e)
        assert e["category"] == "WIRELESS_RX", f"{m} -> {e['category']}"
        assert e["params"]["antennas"] == 4


def test_um2000atd_resolves_to_2in10out():
    e = {"brand": "IPS", "model": "UM2000ATD", "name": "十通道天线分配器"}
    resolve(e)
    assert e["category"] == "ANT_DIST"
    assert e["params"]["inputs"] == 2
    assert e["params"]["outputs"] == 10


# ---------------- 端口展开 ----------------
def test_receiver_ports_4_antennas_2_xlr():
    p = build_project([_rx(), _dist(), _mixer()], name="T")
    rx = [i for i in p.instances if i.category == "WIRELESS_RX"][0]
    ant_in = [x for x in rx.ports if x.signal.value == "RF" and x.role == "in"]
    xlr = [x for x in rx.ports if x.signal.value == "XLR" and x.role == "out"]
    assert len(ant_in) == 4, f"天线口 {len(ant_in)}"
    assert len(xlr) == 2, f"XLR 输出 {len(xlr)}"


def test_dist_ports_2in10out():
    p = build_project([_rx(), _dist(), _mixer()], name="T")
    d = [i for i in p.instances if i.category == "ANT_DIST"][0]
    assert len([x for x in d.ports if x.role == "in"]) == 2
    assert len([x for x in d.ports if x.role == "out"]) == 10


# ---------------- 级联 ----------------
def test_cascade_reserves_2_outs_and_feeds_next_dist():
    """非末台取末尾 2 个出口级联下一台；末台不级联。"""
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(), _dist(),
        _rx(quantity=4),
        _mixer(),
    ]
    p = build_project(entries, name="T")
    casc = [c for c in p.connections if c.note == "分配器级联"]
    # 2 台分配器 -> 1 段级联，每段占 2 个出口
    assert len(casc) == 2, f"级联线 {len(casc)} 条"
    # 级联只从首台发出
    dists = [i for i in p.instances if i.category == "ANT_DIST"]
    assert all(c.from_uid == dists[0].uid for c in casc)
    # 级联用的是末尾两个出口 OUT9 / OUT10
    ids = sorted(int(c.from_port.split("_")[-1]) for c in casc)
    assert ids == [9, 10], ids


def test_four_ports_per_receiver():
    """每台接收机占满其天线口（真分集双通道 = 4 口），按可用出口依次切分。"""
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(), _dist(),
        _rx(quantity=4),
        _mixer(),
    ]
    p = build_project(entries, name="T")
    feeds = [c for c in p.connections if c.note == "天线分配"]
    assert len(feeds) == 16, f"分配线 {len(feeds)} 条（应为 4 台 × 4 口）"
    # 每台接收机恰好收到 4 条
    per_rx = {}
    for c in feeds:
        per_rx[c.to_uid] = per_rx.get(c.to_uid, 0) + 1
    assert len(per_rx) == 4, f"接到的接收机 {len(per_rx)} 台"
    assert set(per_rx.values()) == {4}, per_rx


def test_antennas_feed_only_first_dist():
    """2 支天线只进首台分配器的 2 个进口，第 2 台起由级联供信号。"""
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(), _dist(),
        _rx(quantity=4),
        _mixer(),
    ]
    p = build_project(entries, name="T")
    dists = [i for i in p.instances if i.category == "ANT_DIST"]
    ant = [c for c in p.connections if c.note == "天线→分配器"]
    assert len(ant) == 2, f"天线进线 {len(ant)} 条"
    assert all(c.to_uid == dists[0].uid for c in ant), "天线不应直接进非首台分配器"


def test_last_dist_uses_all_outs():
    """末台无需级联，10 个出口全部可用于接收机（本例接 2 台后余 2 口）。"""
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(),                      # 首台：8 出可用
        _dist(),                      # 末台：10 出可用
        _rx(quantity=4),
        _mixer(),
    ]
    p = build_project(entries, name="T")
    dists = [i for i in p.instances if i.category == "ANT_DIST"]
    last = dists[-1]
    from_last = [c for c in p.connections
                 if c.from_uid == last.uid and c.note == "天线分配"]
    # 4 台接收机：首台吃 2 台（8 口），末台吃 2 台（8 口），故末台发出 8 条
    assert len(from_last) == 8, f"末台分配线 {len(from_last)} 条"


def test_required_dist_count_matches_rule():
    """容量模型：非末台留 2 出口，每 4 口 1 台 -> 每台分配器带 2 台接收机。"""
    assert required_dist_count(1) == 1
    assert required_dist_count(2) == 1      # 末台 10 出即可带 2 台
    assert required_dist_count(3) == 2
    assert required_dist_count(7) == 4      # 智慧剧场实测：7 台 -> 4 台
    assert required_dist_count(8) == 4


def test_wireless_plan_ok_when_enough_dists():
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(), _dist(),
        _rx(quantity=4),
        _mixer(),
    ]
    p = build_project(entries, name="T")
    plan = p.meta.get("wireless_plan") or {}
    assert plan.get("receivers") == 4
    assert plan.get("dists_required") == 2
    assert plan.get("ok") is True


# ---------------- 6.35 混合输出 ----------------
def test_mix_out_port_present_when_feature_set():
    """UM2002 带 mix_out 特性时应多出 1 路 TRS 混合输出。"""
    from avcad.model.specs import build_instances
    insts = build_instances([{
        "category": "WIRELESS_RX", "brand": "IPS", "model": "UM2002",
        "name": "无线双手持麦克风", "quantity": 1,
        "features": ["analog", "control", "mix_out"],
        "params": {"channels": 2, "antennas": 4},
    }])
    rx = insts[0]
    trs = [x for x in rx.ports if x.signal.value == "TRS"]
    xlr = [x for x in rx.ports if x.signal.value == "XLR" and x.role == "out"]
    assert len(trs) == 1, f"TRS 口 {len(trs)}"
    assert len(xlr) == 2
    assert len([x for x in rx.ports if x.signal.value == "RF"]) == 4


# ---------------- 套装拆分 ----------------
def test_set_expand_splits_receiver_and_transmitters(tmp_path):
    """UM2002「1 台接收机 + 2 支话筒」应拆成 1×WIRELESS_RX + 2×WIRELESS_MIC。"""
    import openpyxl
    from avcad.workflow.importers import build_entries
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备名称", "品牌", "型号", "数量"])
    ws.append(["无线双手持麦克风", "IPS", "UM2002", 4])
    path = tmp_path / "um2002.xlsx"
    wb.save(path)
    entries, _ = build_entries(str(path))
    by_cat = {}
    for e in entries:
        by_cat.setdefault(e["category"], 0)
        by_cat[e["category"]] += int(e.get("quantity", 1) or 1)
    assert by_cat.get("WIRELESS_RX") == 4, by_cat   # 4 套 -> 4 台接收机
    assert by_cat.get("WIRELESS_MIC") == 8, by_cat  # 4 套 -> 8 支话筒


# ---------------- AUDIX（跨品牌差异） ----------------
def _audix_project(n_rx=4, channels=2, dists=1):
    entries = [
        {"category": "ANTENNA", "brand": "AUDIX", "model": "ANTDA4161",
         "name": "有源定向天线", "quantity": 2},
        {"category": "ANT_DIST", "brand": "AUDIX", "model": "ADS48",
         "name": "天线分配器", "quantity": dists,
         "params": {"inputs": 2, "outputs": 8, "cascade_outs": 0}},
        {"category": "WIRELESS_RX", "brand": "AUDIX", "model": "AP62",
         "name": "AP62", "quantity": n_rx, "features": ["trs_out"],
         "params": {"channels": channels, "antennas": 2}},
        _mixer(),
    ]
    return build_project(entries, name="AUDIX")


def test_audix_ads48_is_2in8out():
    p = _audix_project()
    d = [i for i in p.instances if i.category == "ANT_DIST"][0]
    assert len([x for x in d.ports if x.role == "in"]) == 2
    assert len([x for x in d.ports if x.role == "out"]) == 8


def test_audix_receiver_uses_2_antennas_not_4():
    """AUDIX 每机固定 2 支天线（A/B），与 IPS 真分集双通道的 4 口不同。"""
    p = _audix_project()
    rx = [i for i in p.instances if i.category == "WIRELESS_RX"][0]
    rf_in = [x for x in rx.ports if x.signal.value == "RF" and x.role == "in"]
    assert len(rf_in) == 2, f"AUDIX 天线口应为 2，实际 {len(rf_in)}"


def test_audix_has_per_channel_trs_not_single_mix():
    """AUDIX 是每通道各 1 路 1/4\" TS（与 XLR 并存），不是整机 1 路混合。"""
    p = _audix_project(channels=2)
    rx = [i for i in p.instances if i.category == "WIRELESS_RX"][0]
    trs = [x for x in rx.ports if x.signal.value == "TRS"]
    xlr = [x for x in rx.ports if x.signal.value == "XLR" and x.role == "out"]
    assert len(trs) == 2 and len(xlr) == 2


def test_audix_ads48_does_not_cascade():
    """ADS48 官方资料未提级联 -> cascade_outs=0，不应产生级联线。"""
    p = _audix_project()
    assert not [c for c in p.connections if c.note == "分配器级联"]
    # 8 出口全给接收机：4 台 × 2 口 = 8 条
    assert len([c for c in p.connections if c.note == "天线分配"]) == 8


def test_audix_required_dist_count():
    """ADS48（8 出、无级联、每机 2 口）单台可带 4 台接收机。"""
    assert required_dist_count(4, antennas_per_rx=2, outputs=8, cascade=0) == 1
    assert required_dist_count(5, antennas_per_rx=2, outputs=8, cascade=0) == 2


def test_audix_catalog_resolution():
    from avcad.parse.product_resolver import resolve
    e = {"brand": "AUDIX", "model": "AP62 BP", "name": "双通道无线话筒(接收机+腰包)"}
    resolve(e)
    assert e["category"] == "WIRELESS_RX"
    assert e["params"]["channels"] == 2
    assert e["params"]["antennas"] == 2
    e2 = {"brand": "AUDIX", "model": "ADS48", "name": "天线分配器"}
    resolve(e2)
    assert e2["params"]["inputs"] == 2 and e2["params"]["outputs"] == 8


def test_insufficient_outs_warns():
    """分配器出口不足时给出明确告警，而不是静默漏接。"""
    entries = [
        {"category": "ANTENNA", "brand": "IPS", "model": "UM2000AP",
         "name": "全指向天线", "quantity": 2},
        _dist(),                      # 单台（末台）：10 出 -> 最多 2 台接收机
        _rx(quantity=5),              # 需要 20 口，远超 10
        _mixer(),
    ]
    p = build_project(entries, name="T")
    warn = p.meta.get("wireless_warnings") or []
    assert warn, "出口不足应产生告警"
    assert "未分配到天线口" in warn[0]
