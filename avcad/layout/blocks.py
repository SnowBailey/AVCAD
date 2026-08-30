"""参数化块几何：根据端口数量计算块尺寸与端口坐标（统一左入右出，顶部为控制/RF）。"""
from __future__ import annotations
from avcad.model.schema import DeviceInstance

ROW_H = 11
HEADER = 32      # 名称 / 品牌 / 型号 三行标题
PAD = 10
MIN_W = 96
MAX_W = 180
SLOT_H = 14      # 卡槽条高度
SW_PORT_PITCH = 14   # 交换机顶部端口间距


def compute_geometry(inst: DeviceInstance):
    left = [p for p in inst.ports if p.side == "left"]
    right = [p for p in inst.ports if p.side == "right"]
    top = [p for p in inst.ports if p.side == "top" or p.side == "bottom"]
    rows = max(len(left), len(right), 1)
    title_len = max(len(inst.name), len(inst.brand), len(inst.model), 8)
    maxlab = max([len(p.label) for p in inst.ports] + [title_len])
    w = max(MIN_W, min(MAX_W, 13 * maxlab + 34))
    # 卡槽条额外占高
    slot_extra = SLOT_H + 4 if inst.slots else 0
    # 交换机：端口全部在顶部，按使用口数横向拉长
    if inst.category == "SWITCH":
        n_top = len([p for p in top if p.side == "top"])
        w = max(MIN_W, SW_PORT_PITCH * (n_top + 1) + 24)
        h = HEADER + PAD + 14
    else:
        # 交换机需容纳上下两排端口（非交换机不影响）
        switch_extra = 18 if inst.category == "SWITCH" and len(top) > 4 else 0
        h = HEADER + rows * ROW_H + PAD + slot_extra + switch_extra
    inst.w = w
    inst.h = h
    for idx, p in enumerate(left):
        p.x = inst.x
        p.y = inst.y + HEADER + idx * ROW_H + ROW_H / 2
    for idx, p in enumerate(right):
        p.x = inst.x + w
        p.y = inst.y + HEADER + idx * ROW_H + ROW_H / 2
    n = len(top)
    for idx, p in enumerate(top):
        if p.side == "top":
            p.x = inst.x + (idx + 1) * (w / (n + 1))
            p.y = inst.y
        else:
            p.x = inst.x + (idx + 1) * (w / (n + 1))
            p.y = inst.y + h
    return inst
