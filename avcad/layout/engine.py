"""布局引擎：阶段=列（信号左→右），列内纵向堆叠；Dante 交换机置于底部侧层。"""
from __future__ import annotations
from avcad.layout.blocks import compute_geometry
from avcad.model.schema import DeviceInstance

MARGIN = 40
COL_GAP = 70
ROW_GAP = 26
SWITCH_GAP = 70


def place(instances: list, chain: list, switches: list) -> dict:
    # 列内实例分组。chain 主序列按阶段分列；SIDE 阶段（IO 接口箱等
    # 侧层设备）单独成列，放在最右侧——它们与调音台/处理器平级，
    # 仅通过 DANTE 经交换机互通，XLR 端由用户按工程需要单独连。
    cols = {c: [] for c in chain}
    side_col = []
    for i in instances:
        if i.stage in cols:
            cols[i.stage].append(i)
        elif i.stage == "SIDE":
            side_col.append(i)
    # 先计算几何：expand_instance 不调用 compute_geometry，w/h 可能还是默认值
    for d in instances:
        compute_geometry(d)
    # 列宽
    MIN_W_REF = 96
    col_w = {}
    for c, devs in cols.items():
        col_w[c] = (max([d.w for d in devs], default=MIN_W_REF)) + COL_GAP

    x_cursor = MARGIN
    col_x = {}
    for c in chain:
        col_x[c] = x_cursor
        x_cursor += col_w[c]

    max_bottom = MARGIN
    for c in chain:
        devs = cols[c]
        x = col_x[c]
        y = MARGIN
        for d in devs:
            d.x = x
            d.y = y
            compute_geometry(d)
            y += d.h + ROW_GAP
        max_bottom = max(max_bottom, y)

    # ★ 侧层设备列：放在主链路右侧，宽度 = 列宽中位数。
    #   此前 SIDE stage 设备不在 cols 里，place() 不会分配坐标，
    #   一直停在 (0,0) 与左下角第一台设备重叠（4F 会议室 DIO 4x4 踩中）。
    if side_col:
        side_col_w = max(MIN_W_REF, max(d.w for d in side_col)) + COL_GAP
        col_x["SIDE"] = x_cursor
        y = MARGIN
        for d in side_col:
            d.x = x_cursor
            d.y = y
            compute_geometry(d)
            y += d.h + ROW_GAP
        max_bottom = max(max_bottom, y)
        x_cursor += side_col_w

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
    return dict(width=width, height=height, col_x=col_x)


def auto_size(inst: DeviceInstance):
    compute_geometry(inst)
