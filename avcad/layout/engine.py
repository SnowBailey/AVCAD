"""布局引擎：阶段=列（信号左→右），列内纵向堆叠；Dante 交换机置于底部侧层。

阳哥规则 2026-08-30：
- 单列纵向模块**不超过 20 个**，超出则并排拆成多列（同一 stage 内）。
- 拆分出的多列用**主干线**在列顶串起来，表示它们同属一级、共享上游信号。
"""
from __future__ import annotations
import math

from avcad.layout.blocks import compute_geometry
from avcad.model.schema import DeviceInstance

MARGIN = 40
COL_GAP = 70
ROW_GAP = 26
SWITCH_GAP = 70
# 单列纵向模块上限；超出则拆列（阳哥规则）
MAX_PER_COLUMN = 20
# 同一 stage 内子列之间的水平间距（比 stage 间距小，视觉上仍属同一级）
SUBCOL_GAP = 36
# 主干线：画在各子列顶部之上，需要预留的纵向空间
TRUNK_SPACE = 40
TRUNK_OFFSET = 24


def _split_columns(cols: dict) -> dict:
    """把每列按 MAX_PER_COLUMN 拆成若干子列（尽量均分）。"""
    subcols = {}
    for c, devs in cols.items():
        n = len(devs)
        if n <= MAX_PER_COLUMN:
            subcols[c] = [devs] if devs else []
            continue
        k = math.ceil(n / MAX_PER_COLUMN)
        per = math.ceil(n / k)
        subcols[c] = [devs[i:i + per] for i in range(0, n, per)]
    return subcols


def place(instances: list, chain: list, switches: list) -> dict:
    # 列内实例分组
    cols = {c: [] for c in chain}
    for i in instances:
        if i.stage in cols:
            cols[i.stage].append(i)
    # 先计算几何：expand_instance 不调用 compute_geometry，w/h 可能还是默认值
    for d in instances:
        compute_geometry(d)

    subcols = _split_columns(cols)
    # 任何一个 stage 拆出多列时，顶部要为主干线留出空间
    need_trunk = any(len(g) > 1 for g in subcols.values())
    top = MARGIN + (TRUNK_SPACE if need_trunk else 0)

    MIN_W_REF = 96
    x_cursor = MARGIN
    col_x, subcol_x = {}, {}
    for c in chain:
        groups = subcols.get(c) or []
        col_x[c] = x_cursor
        xs = []
        if not groups:
            subcol_x[c] = xs
            x_cursor += MIN_W_REF + COL_GAP
            continue
        for g in groups:
            xs.append(x_cursor)
            x_cursor += max(d.w for d in g) + SUBCOL_GAP
        subcol_x[c] = xs
        # 子列末尾的多余间距换回 stage 间距
        x_cursor = x_cursor - SUBCOL_GAP + COL_GAP

    max_bottom = top
    trunks = []
    for c in chain:
        groups = subcols.get(c) or []
        xs = subcol_x.get(c) or []
        for gi, g in enumerate(groups):
            x = xs[gi]
            y = top
            for d in g:
                d.x = x
                d.y = y
                compute_geometry(d)
                y += d.h + ROW_GAP
            max_bottom = max(max_bottom, y)
        if len(groups) > 1:
            # 主干线：横向贯穿各子列顶部中心，各子列顶部引一根短竖线接入
            centers = [xs[i] + max(d.w for d in groups[i]) / 2
                       for i in range(len(groups))]
            trunks.append(dict(stage=c, x1=centers[0], x2=centers[-1],
                               y=top - TRUNK_OFFSET, top=top,
                               drops=centers, count=len(groups)))

    # 兼容：col_x 仍返回该 stage 首个 x 位置
    col_x = {c: (subcol_x.get(c) or [x])[0] for c, x in col_x.items()}

    # 交换机侧层：底部横带
    switch_bottom = max_bottom + SWITCH_GAP
    if switches:
        total_w = x_cursor - COL_GAP + MARGIN
        n = len(switches)
        for s in switches:
            compute_geometry(s)
        total_sw_w = sum(s.w for s in switches)
        gap = max(40, (total_w - total_sw_w) / (n + 1))
        cx = MARGIN
        for s in switches:
            s.x = cx + gap
            s.y = switch_bottom
            cx = s.x + s.w
        switch_bottom = max(switch_bottom + max(s.h for s in switches), switch_bottom)

    width = x_cursor - COL_GAP + MARGIN
    height = switch_bottom + MARGIN
    return dict(width=width, height=height, col_x=col_x,
                subcol_x=subcol_x, trunks=trunks)


def auto_size(inst: DeviceInstance):
    compute_geometry(inst)
