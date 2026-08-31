"""可级联调音台的 LINK 扩展总线（阳哥 2026-08-31：IPS AM860 = 混音器，8进1出，可级联）。

规则：
  · 主库 MIXER 的 ``params.cascade`` > 0 时模板生成一对 LINK_IN / LINK_OUT；
  · 多台**同品牌同型号**的可级联调音台按 BOM 顺序串成一条链；
  · **从机的音频 OUT 不再参与后级连线**——信号已随 LINK 汇总到主机，
    再出线就是同一路信号画两根（这是本测试要守住的核心约束）；
  · 从机的 IN 仍然正常接音源（级联的意义就是扩展话筒数量）。
"""
from __future__ import annotations

import collections

from avcad.core.build import build_project
from avcad.model.schema import Signal


def _am860(n=1):
    """IPS AM860 自动混音器：8 进 1 出，可级联。"""
    return [{"category": "MIXER", "brand": "IPS", "model": "AM860",
             "name": "智能自动混音器", "quantity": 1,
             "params": {"inputs": 8, "outputs": 1, "cascade": 1}}
            for _ in range(n)]


def _mic(n=1, model="MT110"):
    return [{"category": "SOURCE", "brand": "IPS", "model": model,
             "name": "话筒", "quantity": 1} for _ in range(n)]


def _amp():
    # ★ 功放模板的 IN_A 依赖 analog 特性，缺了就不生成输入口（历史坑）
    return {"category": "AMP", "brand": "X", "model": "AMP4",
            "name": "功放", "quantity": 1,
            "features": ["analog"],
            "params": {"inputs": 4, "outputs": 4, "channels": 4}}


def _link_links(p):
    return [c for c in p.connections if c.signal == Signal.LINK]


def test_three_mixers_form_single_link_chain():
    """3 台级联：应串成 2 条 LINK 线（1→2、2→3），首尾不回环。"""
    p = build_project([*_am860(3)], name="T")
    links = _link_links(p)
    assert len(links) == 2, f"3 台级联应 2 条 LINK 线，实为 {len(links)}"

    indeg = collections.Counter(c.to_uid for c in links)
    outdeg = collections.Counter(c.from_uid for c in links)
    mixers = [i for i in p.instances if i.category == "MIXER"]
    assert len(mixers) == 3

    # 恰好一台是链首（只出不进）、一台是链尾（只进不出）
    heads = [m.uid for m in mixers if indeg[m.uid] == 0]
    tails = [m.uid for m in mixers if outdeg[m.uid] == 0]
    assert len(heads) == 1, f"链首应唯一，实为 {heads}"
    assert len(tails) == 1, f"链尾应唯一，实为 {tails}"
    assert heads[0] != tails[0], "不能自环"


def test_cascade_ports_exist_and_ids_unique():
    """cascade>0 才生成 LINK 口，且 LINK_IN / LINK_OUT 的 ID 不撞车。"""
    p = build_project([*_am860(2)], name="T")
    for m in p.instances:
        ids = [pp.id for pp in m.ports]
        assert len(ids) == len(set(ids)), f"{m.model} 端口 ID 撞车：{ids}"
        lin = [pp for pp in m.ports if pp.role == "in" and pp.signal == Signal.LINK]
        lout = [pp for pp in m.ports if pp.role == "out" and pp.signal == Signal.LINK]
        assert len(lin) == 1 and len(lout) == 1, f"{m.model} 应有 1 对 LINK 口"
        assert lin[0].id != lout[0].id

    # 未标 cascade 的调音台不应冒出 LINK 口
    p2 = build_project(
        [{"category": "MIXER", "brand": "X", "model": "M16",
          "name": "普通调音台", "quantity": 1,
          "params": {"inputs": 16, "outputs": 8}}], name="T")
    for m in p2.instances:
        assert not [pp for pp in m.ports if pp.signal == Signal.LINK], \
            "未标 cascade 的调音台不应生成 LINK 口"


def test_slave_outputs_not_wired_downstream():
    """★ 从机的音频 OUT 不得再往后级出线（信号已随 LINK 汇总到主机）。"""
    p = build_project([*_am860(3), _amp()], name="T")
    mixers = [i for i in p.instances if i.category == "MIXER"]
    links = _link_links(p)
    # 链首 = 出了 LINK 但没进 LINK 的那台
    out_uids = {c.from_uid for c in links}
    in_uids = {c.to_uid for c in links}
    head_uid = (out_uids - in_uids).pop()
    slave_uids = in_uids

    audio_out = [c for c in p.connections
                 if c.signal in (Signal.XLR, Signal.AES)
                 and c.from_uid in {m.uid for m in mixers}]
    assert audio_out, "主机应至少有一路音频输出到后级"
    for c in audio_out:
        assert c.from_uid == head_uid, \
            f"从机 {c.from_uid} 也在出音频线，应只由主机 {head_uid} 汇出"


def test_slave_inputs_still_take_sources():
    """级联的意义是扩展话筒数量——从机的 IN 仍要正常接音源。"""
    p = build_project([*_am860(3), *_mic(18)], name="T")
    mixers = [i for i in p.instances if i.category == "MIXER"]
    links = _link_links(p)
    in_uids = {c.to_uid for c in links}          # 两台从机
    head_uid = ({c.from_uid for c in links} - in_uids).pop()

    per_mixer = collections.Counter(
        c.to_uid for c in p.connections
        if c.signal in (Signal.XLR, Signal.AES)
        and c.to_uid in {m.uid for m in mixers})
    # 18 只话筒 / 3 台 × 8 进 = 24 路容量，每台都该分到话筒
    for m in mixers:
        assert per_mixer[m.uid] > 0, f"{m.uid} 一台话筒都没接到"
    assert sum(per_mixer.values()) == 18, \
        f"18 只话筒应全部接入，实接 {sum(per_mixer.values())}"
    # 主机既接自己的话筒、也接从机 LINK
    assert per_mixer[head_uid] > 0
