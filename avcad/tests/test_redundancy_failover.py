"""冗余分级语义 + 主备 failover + 孤立音源救援的回归测试。

2026-08-31 补齐的语义（阳哥「你来判断最佳方案」后定，见
`schema.REDUNDANCY_SCOPE`）：

    DEVICE_BACKUP     设备级热备（复制调音台），画主备 failover 线
    PROCESSOR_BACKUP  处理器热备（复制处理器），画主备 failover 线
    LINK_BACKUP       链路冗余（冗余载体是交换机），**不画**设备间 failover 线
    FULL_CHAIN        全链路（调音台+处理器+双交换机），画 failover 线

★ 级别在两条入口路径上的含义不同，别混为一谈：
  - 候选 / API 路径（`_apply_redundancy`）：级别决定**复制哪些类别**；
  - 清单 CSV 路径：用户已经用「冗余」列指明了哪几台是主备，级别只描述
    **怎么冗余**（是否画 failover 线、是否要双交换机），不复制设备——
    清单是造价清单，买了 1 台就不该凭空变 2 台。

本文件的由来：用 `scripts/probe_link_coverage.py` 扫描 10 份真实清单，发现
`_failover` 与 `_orphan_sources_rescue` **产出 0 条连线**——即从未被真实数据
覆盖。构造场景验证后确认两者行为正确，故补测试锁住，避免日后改坏。

★ 真实清单全绿 ≠ 代码正确，只说明这条路径没被走到。
"""
from __future__ import annotations

from avcad.core.build import build_project
from avcad.model.schema import Signal, Redundancy, normalize_redundancy


def _dev(cat, model="X", n=1, **kw):
    d = {"category": cat, "brand": "IPS", "model": model,
         "name": model, "quantity": n}
    d.update(kw)
    return d


def _proc(redundancy=None):
    return _dev("PROCESSOR", "GMN1208D", features=["analog", "control"],
                params={"inputs": 8, "outputs": 8},
                **({"redundancy": redundancy} if redundancy else {}))


def _mixer():
    return _dev("MIXER", "QU-16", features=["analog"],
                params={"inputs": 16, "outputs": 8})


def _backbone(with_redundancy=False):
    return [
        _dev("SOURCE", "MT110", 2, features=["analog"],
             params={"outputs": 1}),
        _mixer(),
        _proc("PROCESSOR_BACKUP") if with_redundancy else _proc(),
        _proc("PROCESSOR_BACKUP") if with_redundancy else _proc(),
        _dev("AMP", "DA250Q", features=["analog", "control"],
             params={"channels": 4}),
        _dev("SPEAKER", "CI600", 2,
             params={"impedance_ohm": 8, "power_w": 80}),
    ]


# ---------------------------------------------------------------- failover


def test_redundant_processors_are_paired():
    """两台带冗余标记的处理器应互为主备。"""
    p = build_project(_backbone(with_redundancy=True), name="T")
    procs = [i for i in p.instances if i.category == "PROCESSOR"]
    assert len(procs) == 2
    main = [i for i in procs if not i.is_backup]
    bak = [i for i in procs if i.is_backup]
    assert len(main) == 1 and len(bak) == 1, "应有且仅有一主一备"
    assert main[0].redundant_group, "主备组名为空"
    assert main[0].redundant_group == bak[0].redundant_group
    assert main[0].pair == bak[0].uid and bak[0].pair == main[0].uid


def test_failover_link_goes_main_to_backup_over_audio_cable():
    """failover 线：主 -> 备，且只走 XLR/AES，绝不走 Dante。"""
    p = build_project(_backbone(with_redundancy=True), name="T")
    fo = [c for c in p.connections if c.note == "主备failover"]
    assert len(fo) == 1, f"应 1 条 failover 线，实为 {len(fo)}"

    c = fo[0]
    procs = {i.uid: i for i in p.instances if i.category == "PROCESSOR"}
    assert c.from_uid in procs and c.to_uid in procs
    assert not procs[c.from_uid].is_backup, "failover 起点应是主机"
    assert procs[c.to_uid].is_backup, "failover 终点应是备机"
    assert c.signal in (Signal.XLR, Signal.AES), \
        f"failover 只走模拟/数字音频线，实为 {c.signal}"
    assert c.signal != Signal.DANTE, "Dante 一律经交换机，不得设备间直连"
    assert c.role == "backup"


def test_no_redundancy_means_no_backup_links():
    """不带冗余标记时不应产生任何 backup 角色连线。"""
    p = build_project(_backbone(with_redundancy=False), name="T")
    assert not [c for c in p.connections if c.role == "backup"], \
        "无冗余却出现了 backup 连线"
    assert not [i for i in p.instances if i.is_backup]


def test_backup_path_follows_same_direction_as_primary():
    """备路径的连线方向必须与主路径一致（不能反向接回调音台）。"""
    p = build_project(_backbone(with_redundancy=True), name="T")
    procs = {i.uid: i for i in p.instances if i.category == "PROCESSOR"}
    mixer_uid = next(i.uid for i in p.instances if i.category == "MIXER")

    main = next(uid for uid, i in procs.items() if not i.is_backup)
    bak = next(uid for uid, i in procs.items() if i.is_backup)

    # 主处理器 -> 调音台，则备处理器也必须 -> 调音台（方向一致）
    main_to_mixer = [c for c in p.connections
                     if c.from_uid == main and c.to_uid == mixer_uid]
    bak_to_mixer = [c for c in p.connections
                    if c.from_uid == bak and c.to_uid == mixer_uid]
    assert main_to_mixer, "主处理器未连调音台"
    assert bak_to_mixer, "备处理器未连调音台（备路径缺失）"
    # 反向：调音台不该反过来送进处理器
    assert not [c for c in p.connections
                if c.from_uid == mixer_uid and c.to_uid in (main, bak)], \
        "调音台反向送进了处理器，方向错了"


# ------------------------------------------------------- orphan rescue


def test_overflow_sources_land_on_free_ports_only():
    """音源数超过处理器输入时，多余音源应落到**空闲**口，不挤占已用口。"""
    p = build_project([
        _dev("SOURCE", "MT110", 6, features=["analog"],
             params={"outputs": 1}),
        _dev("PROCESSOR", "GMN1208D", features=["analog"],
             params={"inputs": 4, "outputs": 4}),
        _mixer(),
        _dev("AMP", "DA250Q", features=["analog", "control"],
             params={"channels": 4}),
        _dev("SPEAKER", "CI600", 2,
             params={"impedance_ohm": 8, "power_w": 80}),
    ], name="T")

    proc = next(i for i in p.instances if i.category == "PROCESSOR")
    mixer = next(i for i in p.instances if i.category == "MIXER")
    rescued = [c for c in p.connections if c.note == "音源直入"]
    assert rescued, "溢出的 2 路音源没有被救援（处理器只有 4 路输入）"
    assert len(rescued) == 2, f"应救援 2 路，实为 {len(rescued)}"

    # 关键：救援落点不得与处理器已占用口冲突
    used_by_proc = {c.to_port for c in p.connections if c.to_uid == proc.uid}
    for c in rescued:
        assert c.to_uid == mixer.uid, "溢出音源应直入调音台"
        assert c.to_port not in used_by_proc, \
            f"救援落点 {c.to_port} 与其它进线冲突"


def test_orphan_rescue_matches_signal_type():
    """救援时信号类型必须匹配（XLR 音源不能落到 SPEAKER 口）。"""
    p = build_project([
        _dev("SOURCE", "MT110", 6, features=["analog"],
             params={"outputs": 1}),
        _dev("PROCESSOR", "GMN1208D", features=["analog"],
             params={"inputs": 4, "outputs": 4}),
        _mixer(),
        _dev("AMP", "DA250Q", features=["analog", "control"],
             params={"channels": 4}),
        _dev("SPEAKER", "CI600", 2,
             params={"impedance_ohm": 8, "power_w": 80}),
    ], name="T")
    for c in p.connections:
        if c.note != "音源直入":
            continue
        src = next(i for i in p.instances if i.uid == c.from_uid)
        dst = next(i for i in p.instances if i.uid == c.to_uid)
        sp = next(q for q in src.ports if q.id == c.from_port)
        dp = next(q for q in dst.ports if q.id == c.to_port)
        assert sp.signal == dp.signal, \
            f"救援连线信号不匹配：{sp.signal} -> {dp.signal}"


# ------------------------------------------------- 冗余分级语义（清单路径）


def _dante_backbone(mixer_redundancy=None, n_mixer=2):
    """带 Dante 的骨架：若干台调音台（可统一标冗余）+ 音源 + 功放 + 音箱。"""
    return [
        _dev("SOURCE", "MT110", 2, features=["analog"],
             params={"outputs": 1}),
        _dev("MIXER", "TF5", n_mixer, features=["analog", "dante", "control"],
             params={"inputs": 16, "outputs": 8},
             **({"redundancy": mixer_redundancy} if mixer_redundancy else {})),
        _dev("AMP", "DA250Q", features=["analog", "control"],
             params={"channels": 4}),
        _dev("SPEAKER", "CI600", 2,
             params={"impedance_ohm": 8, "power_w": 80}),
    ]


def _failover_links(p):
    return [c for c in p.connections if c.note == "主备failover"]


def _with_real_switch(backbone, n_switch=1, sw_redundancy=None):
    """给骨架挂上「清单里明确配了」的真交换机（模拟造价清单自带网络交换机）。"""
    return list(backbone) + [
        _dev("SWITCH", "AIM-24MG6XF", n_switch,
             params={"ports": 24},
             **({"redundancy": sw_redundancy} if sw_redundancy else {})),
    ]


def test_link_backup_pair_has_no_failover_but_dual_switch():
    """链路冗余：主备各走一台交换机，设备之间**不画** failover 线。"""
    p = build_project(_dante_backbone("LINK_BACKUP"), name="T")
    baks = [i for i in p.instances if i.is_backup]
    assert len(baks) == 1 and baks[0].category == "MIXER", "2 台调音台应配成一主一备"
    assert not _failover_links(p), "链路冗余不得画设备间 failover 直连线"
    assert len(p.switches) == 2, "链路冗余必须双交换机"


def test_device_backup_pair_draws_failover_line():
    """设备级热备：要画「主 → 备」的 failover 线。"""
    p = build_project(_dante_backbone("DEVICE_BACKUP"), name="T")
    assert len(_failover_links(p)) == 1, "设备级热备应画 1 条 failover 线"


def test_redundancy_levels_differ_in_csv_path():
    """清单路径上 LINK_BACKUP 与 DEVICE_BACKUP 必须产出不同的图。

    回归背景：此前三档在清单路径上产出完全相同的图（级别值被当布尔丢弃）。
    """
    link = build_project(_dante_backbone("LINK_BACKUP"), name="T")
    dev = build_project(_dante_backbone("DEVICE_BACKUP"), name="T")
    assert len(_failover_links(link)) == 0 and len(_failover_links(dev)) == 1
    assert len(link.connections) < len(dev.connections), \
        "两档连线数应当不同，否则级别又退化成布尔了"


def test_single_device_redundancy_warns_instead_of_silently_ignored():
    """★ 同类只有 1 台却标了冗余：必须告警，不能静默失效。"""
    p = build_project(_dante_backbone("FULL_CHAIN", n_mixer=1), name="T")
    warns = p.meta.get("redundancy_warnings", [])
    assert warns, "标了冗余却没能成对，必须告警（此前是完全静默）"
    assert any("只有 1 台" in w for w in warns), warns
    assert not [i for i in p.instances if i.is_backup], "不应凭空造出备机"
    # 告警要能进校验报告
    assert any(i.code == "REDUNDANCY" for i in p.issues), \
        "冗余告警未进入 issues，报告里看不到"


def test_more_than_two_redundant_devices_warns():
    """同类 3 台标冗余：只取前 2 台配主备，其余要点名告警。"""
    p = build_project(_dante_backbone("DEVICE_BACKUP", n_mixer=3), name="T")
    baks = [i for i in p.instances if i.is_backup]
    assert len(baks) == 1, "3 台里只应有 1 台被标为备机"
    warns = p.meta.get("redundancy_warnings", [])
    assert any("只取前 2 台" in w for w in warns), warns


def test_chinese_redundancy_values_are_recognized():
    """清单「冗余」列写中文必须能识别——此前 `Redundancy(str(v).upper())`
    遇到中文会 ValueError 崩掉整张清单（该列别名里就有「冗余」「主备」）。
    """
    assert normalize_redundancy("主备") == Redundancy.DEVICE_BACKUP
    assert normalize_redundancy("调音台主备") == Redundancy.DEVICE_BACKUP
    assert normalize_redundancy("处理器主备") == Redundancy.PROCESSOR_BACKUP
    assert normalize_redundancy("链路冗余") == Redundancy.LINK_BACKUP
    assert normalize_redundancy("全链路") == Redundancy.FULL_CHAIN
    # 容忍空格/下划线，以及空值与无法识别的写法（降级为 NONE 而非抛异常）
    assert normalize_redundancy("processor backup") == Redundancy.PROCESSOR_BACKUP
    assert normalize_redundancy("") == Redundancy.NONE
    assert normalize_redundancy(None) == Redundancy.NONE
    assert normalize_redundancy("随便写的") == Redundancy.NONE
    assert normalize_redundancy(Redundancy.FULL_CHAIN) == Redundancy.FULL_CHAIN


def test_chinese_redundancy_end_to_end():
    """端到端：清单写「主备」应真的配成一主一备并画 failover 线。"""
    p = build_project(_dante_backbone("主备"), name="T")
    baks = [i for i in p.instances if i.is_backup]
    assert len(baks) == 1 and baks[0].category == "MIXER"
    assert len(_failover_links(p)) == 1


def test_single_real_switch_is_cloned_when_level_requires_dual():
    """★ 清单只配 1 台交换机 + 冗余级别要求双交换机 → 必须补一台备机。

    回归背景：此前 `switches = real_switches` 直接短路返回，LINK_BACKUP /
    FULL_CHAIN 静默退化成单链路，只在报告里留一条 SPOF 警告，图上主备设备
    仍挤在同一台交换机上，冗余形同虚设。
    """
    for lvl in ("LINK_BACKUP", "FULL_CHAIN"):
        p = build_project(_with_real_switch(_dante_backbone(lvl), 1), name="T")
        assert len(p.switches) == 2, f"{lvl} 应补出双交换机，实际 {len(p.switches)}"
        # 备机要继承主交换机的品牌/型号/端口数，不是凭空造一台规格不同的
        prim, bak = p.switches
        assert bak.model == prim.model and bak.brand == prim.brand
        assert len(bak.ports) == len(prim.ports), "备交换机端口数应与主交换机一致"
        assert bak.is_backup and bak.pair == prim.uid
        assert not any(i.code == "SPOF" for i in p.issues), \
            f"{lvl} 补齐双交换机后不该再有 SPOF 告警：{_spofs(p)}"


def test_two_real_switches_are_never_cloned():
    """清单已经配了 2 台交换机时不得再补第三台（设备数量是事实，不能凭空加）。"""
    for lvl in ("LINK_BACKUP", "FULL_CHAIN", "NONE"):
        p = build_project(_with_real_switch(_dante_backbone(lvl), 2), name="T")
        assert len(p.switches) == 2, f"{lvl} 不该多出交换机，实际 {len(p.switches)}"


def test_single_switch_stays_single_without_redundancy():
    """无冗余时清单配几台就是几台（NONE 不得触发克隆）。"""
    p = build_project(_with_real_switch(_dante_backbone("NONE"), 1), name="T")
    assert len(p.switches) == 1, "无冗余不该凭空补备交换机"


def test_cloned_backup_switch_actually_carries_traffic():
    """克隆出的备交换机必须真的接上备设备的 Dante，不能是孤立节点。"""
    p = build_project(_with_real_switch(_dante_backbone("LINK_BACKUP"), 1), name="T")
    bak_sw = [s for s in p.switches if s.is_backup]
    assert len(bak_sw) == 1
    sw_uid = bak_sw[0].uid
    assert any(c.from_uid == sw_uid or c.to_uid == sw_uid for c in p.connections), \
        "备交换机没接上任何连线，成了孤立节点"


def _spofs(p):
    return [i.msg for i in p.issues if i.code == "SPOF"]


def test_no_orphan_left_when_capacity_is_enough():
    """容量足够时不该出现救援连线（避免误触发）。"""
    p = build_project([
        _dev("SOURCE", "MT110", 3, features=["analog"],
             params={"outputs": 1}),
        _dev("PROCESSOR", "GMN1208D", features=["analog"],
             params={"inputs": 8, "outputs": 8}),
        _mixer(),
    ], name="T")
    assert not [c for c in p.connections if c.note == "音源直入"], \
        "容量足够却触发了救援"
    # 每路音源都该有出线
    sources = [i for i in p.instances if i.category == "SOURCE"]
    linked = {c.from_uid for c in p.connections}
    assert all(s.uid in linked for s in sources), "存在孤立音源"
