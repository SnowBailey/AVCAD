"""端口几何（side）的一致性守卫。

背景（2026-08-31）：前端 `index.html` 的 `SIDES` 常量提供 4 个选项
（left / right / top / bottom），但 `layout/blocks.py` 把 top 和 bottom
**混成一个序列统一均分宽度**：

    top = [p for p in inst.ports if p.side == "top" or p.side == "bottom"]
    n = len(top)
    for idx, p in enumerate(top):
        p.x = inst.x + (idx + 1) * (w / (n + 1))     # ← 按列表顺序错位均分

于是排列靠前的顶口全落在左半边、靠后的底口全落在右半边。2 顶 2 底实测
顶口横向占比 0.20/0.40、底口 0.60/0.80，一眼看上去就是画错了。

为什么这个 bug 活到今天：规格库 48 个端口模板和图例库 64 个端口里
**没有任何一个 bottom**，这段分支从未被真实数据走到过。前端允许选 bottom，
所以用户随时能触发——属于「零命中但可触达且确实错」的代码。

修复：top / bottom 各自成组、各自均分。纯 top 时新旧公式等价
（本文件逐值比对守住这一点），所以对存量图纸零影响。

新增 side 值时要同步 3 处：
  1. avcad/ui/static/index.html —— 前端常量 SIDES
  2. avcad/layout/blocks.py     —— 坐标分配
  3. avcad/render/draw.py       —— 标签位置（_draw_ports 的 else 分支兜底）
"""
from __future__ import annotations

import os
import re

from avcad.layout.blocks import compute_geometry
from avcad.model.schema import (ConcretePort, DeviceInstance,
                                VALID_SIDES, VALID_ROLES)


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INDEX_HTML = os.path.join(_ROOT, "avcad", "ui", "static", "index.html")


def _frontend_const(name):
    """从 index.html 里抠出 `const <name> = [...]`。"""
    assert os.path.exists(_INDEX_HTML), f"找不到前端文件：{_INDEX_HTML}"
    src = open(_INDEX_HTML, encoding="utf-8").read()
    m = re.search(rf"const\s+{name}\s*=\s*\[(.*?)\]\s*;", src, re.S)
    assert m, (
        f"index.html 里找不到 `const {name} = [...]`。若常量改名了请同步本守卫，"
        f"别直接删掉这条断言——它防的是「前端下拉漏值导致端口字段被静默改写」。"
    )
    return re.findall(r'"([^"]+)"', m.group(1))


def _frontend_sides():
    return _frontend_const("SIDES")


def test_frontend_sides_and_roles_match_schema_constants():
    """★ 前端下拉取值必须等于 schema 里的权威常量。

    两边的差集都会出事：
    - 前端少了 -> 该值在下拉里选不中，编辑端口时被静默改写成首项
      （RS232 在 SIGNALS 上已经中过一次招）
    - 前端多了 -> 用户能选出后端布局不认识的方向，端口飞出图纸
    """
    for name, valid in (("SIDES", VALID_SIDES), ("ROLES", VALID_ROLES)):
        front = _frontend_const(name)
        assert sorted(front) == sorted(valid), (
            f"index.html 的 {name} 与 schema.{ 'VALID_SIDES' if name == 'SIDES' else 'VALID_ROLES' } 不一致：\n"
            f"  前端   {sorted(front)}\n"
            f"  后端   {sorted(valid)}\n"
            f"  前端多 {sorted(set(front) - set(valid))}（后端不认，端口会画错）\n"
            f"  前端少 {sorted(set(valid) - set(front))}（选不中，会被静默改写）"
        )
        assert len(front) == len(set(front)), f"{name} 有重复项：{front}"


def _inst(sides, category="MIXER"):
    inst = DeviceInstance(uid="t1", name="测试设备", brand="B", model="M",
                          category=category, params={})
    inst.ports = [
        ConcretePort(id=f"t1:p{i}", uid="t1", side=s, signal="XLR",
                     label=f"P{i}", index=i, role="io")
        for i, s in enumerate(sides)
    ]
    compute_geometry(inst)
    return inst


def _frac_x(inst, p):
    return (p.x - inst.x) / inst.w


def _legacy_geometry(sides):
    """复刻修复前的实现，用于证明「纯 top 时新旧完全等价」。"""
    inst = _inst(sides)
    w, h = inst.w, inst.h
    group = [p for p in inst.ports if p.side in ("top", "bottom")]
    n = len(group)
    for idx, p in enumerate(group):
        p.x = inst.x + (idx + 1) * (w / (n + 1))
        p.y = inst.y if p.side == "top" else inst.y + h
    return inst


def test_every_frontend_side_lands_on_its_own_edge():
    """★ 前端下拉给出的每个 side，布局都必须把端口放到对应边界上。

    这是「前端允许选、后端却没实现」的兜底网：新增 side 值时若忘了改
    compute_geometry，端口坐标会停在默认值（0,0），图纸上直接飞出去。
    """
    for side in _frontend_sides():
        inst = _inst([side])
        (p,) = inst.ports
        if side == "left":
            assert abs(p.x - inst.x) < 1e-6, f"{side} 端口应贴在左边界"
        elif side == "right":
            assert abs(p.x - (inst.x + inst.w)) < 1e-6, f"{side} 端口应贴在右边界"
        elif side == "top":
            assert abs(p.y - inst.y) < 1e-6, f"{side} 端口应贴在上边界"
        elif side == "bottom":
            assert abs(p.y - (inst.y + inst.h)) < 1e-6, f"{side} 端口应贴在下边界"
        else:
            raise AssertionError(
                f"前端新增了 side={side!r}，本守卫不认识它。"
                f"请确认 avcad/layout/blocks.py 已支持该方向，并补上断言。"
            )
        # 端口不许跑到块外面（横向必须在块宽内）
        assert inst.x <= p.x <= inst.x + inst.w, f"{side} 端口横向越界"


def test_top_and_bottom_each_share_full_width():
    """★ 核心回归：顶口与底口**各自**均分整个宽度，不是混在一起错位均分。

    修复前 2 顶 2 底实测顶口占比 0.20/0.40、底口 0.60/0.80（顶口全挤左半边）。
    """
    inst = _inst(["top", "top", "bottom", "bottom"])
    tops = [p for p in inst.ports if p.side == "top"]
    bots = [p for p in inst.ports if p.side == "bottom"]
    n = len(tops)

    want = [(i + 1) / (n + 1) for i in range(n)]
    got_t = [_frac_x(inst, p) for p in tops]
    got_b = [_frac_x(inst, p) for p in bots]

    for got, name in ((got_t, "顶口"), (got_b, "底口")):
        for g, wv in zip(got, want):
            assert abs(g - wv) < 1e-6, (
                f"{name}横向占比应为 {wv:.2f}，实际 {g:.2f}（全部：{got}）。"
                f"顶口与底口必须各自独立均分宽度"
            )
    # 顶口与底口应落在同一组横向位置上（上下对称）
    assert got_t == got_b, (
        f"顶口与底口横向位置应对齐，实际顶 {got_t} / 底 {got_b}"
    )


def test_pure_top_geometry_is_unchanged_from_legacy():
    """存量等价性：只有 top 端口时，新实现必须与修复前逐值一致。

    规格库与图例库里目前没有任何 bottom 端口，所以这条断言等价于
    「本次修复对全部存量图纸零影响」。
    """
    cases = (
        ["top"],
        ["top", "top"],
        ["top"] * 5,
        ["left", "right", "top", "top"],
        ["left", "left", "right", "right", "top", "top", "top"],
    )
    for sides in cases:
        new, old = _inst(sides), _legacy_geometry(sides)
        assert new.w == old.w and new.h == old.h, f"{sides} 块尺寸变了"
        for a, b in zip(new.ports, old.ports):
            assert abs(a.x - b.x) < 1e-9 and abs(a.y - b.y) < 1e-9, (
                f"{sides} 的 {a.label} 坐标变了："
                f"新 ({a.x},{a.y}) vs 旧 ({b.x},{b.y})。"
                f"修复不应影响纯 top 的存量布局"
            )


def test_left_right_ports_stack_vertically():
    """左右侧端口按行向下排列，行高固定，且第一个口要避开标题区。"""
    inst = _inst(["left", "left", "left"])
    ys = [p.y for p in inst.ports]
    assert ys == sorted(ys), "左侧端口应自上而下排列"
    gaps = [round(b - a, 6) for a, b in zip(ys, ys[1:])]
    assert len(set(gaps)) == 1, f"行距应均匀，实际 {gaps}"
    assert ys[0] > inst.y, "第一个端口不能压在标题区上"


def test_bottom_ports_do_not_shift_top_ports():
    """给设备加底口，不应挪动已有顶口的横向位置。"""
    without = _inst(["top", "top"])
    with_bottom = _inst(["top", "top", "bottom"])
    for a, b in zip([p for p in without.ports if p.side == "top"],
                    [p for p in with_bottom.ports if p.side == "top"]):
        assert abs(a.x - b.x) < 1e-9, (
            f"加了底口后顶口 {a.label} 横向位置变了：{a.x} -> {b.x}"
        )
