"""主备 failover 与孤立音源救援的回归测试。

背景（2026-08-31）：用 `scripts/probe_link_coverage.py` 扫描 10 份真实清单，
发现 `_failover` 与 `_orphan_sources_rescue` **产出 0 条连线**——即从未被
真实数据覆盖。构造场景验证后确认两者行为正确，故补测试锁住，避免日后改坏。

★ 真实清单全绿 ≠ 代码正确，只说明这条路径没被走到。
"""
from __future__ import annotations

from avcad.core.build import build_project
from avcad.model.schema import Signal


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
