"""把工程对象画成与原语：设备块、端口、标签、连线。SVG/DXF 渲染器共用。"""
from __future__ import annotations
from avcad.model.schema import Signal, Redundancy, signal_color, signal_layer, signal_ltype
from avcad.wires.router import _port
from avcad.render.primitives import Rect, Line, Text, Port, Polyline, Circle

FRAME = {"primary": "#378add", "backup": "#5dcaa5", "single": "#b4b2a9"}

# 匿名模式（隐藏品牌/型号）下的通用名称与代号前缀
CATEGORY_CN = {
    "SOURCE": "音源", "WIRELESS_MIC": "无线话筒", "WIRELESS_RX": "无线接收机",
    "ANTENNA": "天线", "ANT_DIST": "天线分配器", "MIXER": "调音台",
    "PROCESSOR": "音频处理器", "SPEAKER_MGR": "音箱管理器", "AMP": "功率放大器",
    "SPEAKER": "扬声器", "IO": "音频接口箱", "SWITCH": "网络交换机",
}
CATEGORY_CODE = {
    "SOURCE": "SRC", "WIRELESS_MIC": "WMIC", "WIRELESS_RX": "WRX",
    "ANTENNA": "ANT", "ANT_DIST": "ADIST", "MIXER": "MIX",
    "PROCESSOR": "DSP", "SPEAKER_MGR": "SMGR", "AMP": "AMP",
    "SPEAKER": "SPK", "IO": "IO", "SWITCH": "SW",
}


def _lut(project):
    return {i.uid: i for i in project.instances + project.switches}


def assign_anon_codes(project):
    """匿名模式：给每个实例分配「类别代号-序号」（如 MIX-01 / SPK-03）。

    序号按类别内出现顺序递增；同一主备对共用一个代号（备用加 -B 后缀）。
    结果写到 inst.anon_code，供标题与出图说明使用。
    """
    counter = {}
    for inst in project.instances + project.switches:
        code = CATEGORY_CODE.get(inst.category, inst.category[:3].upper() or "DEV")
        counter[code] = counter.get(code, 0) + 1
        inst.anon_code = f"{code}-{counter[code]:02d}"
        if getattr(inst, "is_backup", False):
            inst.anon_code += "-B"
    return project


def _draw_slots(canvas, inst):
    """绘制可扩展卡槽条（HY/MY/RY），位于模块底部。"""
    if not inst.slots:
        return
    SLOT_H = 14
    slot_y = inst.y + inst.h - SLOT_H - 2
    # 卡槽条背景
    canvas.add(Rect(
        x=inst.x + 4, y=slot_y, w=inst.w - 8, h=SLOT_H,
        layer="DEVICES", color="#4a4a42", fill="#1e1e1a", width=1.0, tag=inst.uid))
    # 平铺所有卡槽位
    all_slots = []
    for s in inst.slots:
        count = int(s.get("count", 1))
        label = s.get("label", s.get("type", "SLOT"))
        for i in range(count):
            all_slots.append(f"{label}{i + 1}")
    n = len(all_slots)
    if n == 0:
        return
    gap = 3
    sw = (inst.w - 8 - gap * (n + 1)) / n
    for i, lab in enumerate(all_slots):
        sx = inst.x + 4 + gap + i * (sw + gap)
        canvas.add(Rect(
            x=sx, y=slot_y + 2, w=sw, h=SLOT_H - 4,
            layer="DEVICES", color="#6a6a60", fill="#2a2a24", width=0.8, tag=inst.uid))
        canvas.add(Text(
            x=sx + sw / 2, y=slot_y + 9, text=lab,
            layer="LABELS", color="#a4a29a", size=6, anchor="middle"))


def _draw_title(canvas, inst, anon=False):
    """标题：名称 / 品牌 / 型号 各占一行，位于模块顶部标题区（高度 32px）。

    anon=True（匿名模式）时不显示品牌与型号：第一行保留清单名称（用户自填、
    通常不含厂商信息），第二行显示类别代号（如 MIX-01），第三行留空。

    水平方向用 anchor="middle" + x=模块中心 实现左右居中（与 DXF 的
    BOTTOM_CENTER 对齐一致）；垂直方向钉在标题区，不随模块高度居中。
    """
    cx = inst.x + inst.w / 2
    if anon:
        name = inst.name or CATEGORY_CN.get(inst.category, inst.category)
        code = getattr(inst, "anon_code", "") or CATEGORY_CN.get(inst.category, inst.category)
        canvas.add(Text(
            x=cx, y=inst.y + 10, text=name[:26],
            layer="LABELS", color="#f1efe8", size=9, anchor="middle", bold=True, tag=inst.uid))
        canvas.add(Text(
            x=cx, y=inst.y + 20, text=code[:26],
            layer="LABELS", color="#c4c2b8", size=8, anchor="middle", tag=inst.uid))
        return
    name = inst.name or ""
    brand = inst.brand or ""
    model = inst.model or ""
    # 第一行：设备名称（清单名称）
    canvas.add(Text(
        x=cx, y=inst.y + 10, text=name[:26],
        layer="LABELS", color="#f1efe8", size=9, anchor="middle", bold=True, tag=inst.uid))
    # 第二行：品牌
    if brand:
        canvas.add(Text(
            x=cx, y=inst.y + 20, text=brand[:26],
            layer="LABELS", color="#c4c2b8", size=8, anchor="middle", tag=inst.uid))
    # 第三行：型号
    if model:
        canvas.add(Text(
            x=cx, y=inst.y + 29, text=model[:30],
            layer="LABELS", color="#9a978d", size=7, anchor="middle", tag=inst.uid))


def _prepare_switch_ports(inst):
    """交换机所有 Dante 端口统一放在上边缘，方便从上方主线接入。"""
    ports = [p for p in inst.ports if p.signal == Signal.DANTE]
    n = len(ports)
    if n == 0:
        return
    slot_w = (inst.w - 16) / n
    for i, p in enumerate(ports):
        px = inst.x + 8 + slot_w * i + slot_w / 2
        p.x = px
        p.y = inst.y
        p.side = "top"


def _draw_switch(canvas, inst, anon=False):
    """绘制交换机模块主体（端口位置已预先计算）。"""
    fr = FRAME["single"]
    canvas.add(Rect(
        x=inst.x, y=inst.y, w=inst.w, h=inst.h,
        layer="DEVICES", color=fr, fill="#26261f", width=1.4, tag=inst.uid))
    _draw_title(canvas, inst, anon)


def draw_devices(canvas, project, anon=False):
    """绘制模块主体（含标题、卡槽），不绘制端口。

    anon=True 时先分配匿名代号，标题改为「清单名称 + 类别代号」。
    """
    if anon:
        assign_anon_codes(project)
    for inst in project.instances + project.switches:
        if inst.category == "SWITCH":
            _prepare_switch_ports(inst)
            _draw_switch(canvas, inst, anon)
            continue
        fr = FRAME["single"]
        if inst.redundancy != Redundancy.NONE:
            fr = FRAME["backup"] if inst.is_backup else FRAME["primary"]
        canvas.add(Rect(
            x=inst.x, y=inst.y, w=inst.w, h=inst.h,
            layer="DEVICES", color=fr, fill="#26261f", width=1.4, tag=inst.uid))
        _draw_title(canvas, inst, anon)
        _draw_slots(canvas, inst)


def draw_ports(canvas, project):
    """在最上层绘制端口与标签，确保连线终点清晰可见。
    端口字母统一画在模块内部（主标签），外缘只保留微小的端口色点。"""
    _HEADER = 32
    for inst in project.instances + project.switches:
        for p in inst.ports:
            canvas.add(Port(
                x=p.x, y=p.y, r=2.4, color=signal_color(p.signal), layer="PORTS", tag=p.id))
            if p.air:
                continue
            # 主标签：统一放在模块内部，左右侧贴边、顶底避开标题区
            if p.side == "left":
                canvas.add(Text(
                    x=inst.x + 7, y=p.y + 3, text=p.label, layer="LABELS", color="#f5f5ec",
                    size=8, anchor="start", bold=True))
            elif p.side == "right":
                canvas.add(Text(
                    x=inst.x + inst.w - 7, y=p.y + 3, text=p.label, layer="LABELS",
                    color="#f5f5ec", size=8, anchor="end", bold=True))
            elif p.side == "top":
                canvas.add(Text(
                    x=p.x, y=inst.y + _HEADER + 7, text=p.label, layer="LABELS",
                    color="#f5f5ec", size=8, anchor="middle", bold=True))
            else:  # bottom
                canvas.add(Text(
                    x=p.x, y=inst.y + inst.h - 5, text=p.label, layer="LABELS",
                    color="#f5f5ec", size=8, anchor="middle", bold=True))


def _endpoint(lut, uid, port_id):
    """返回端口精确坐标及所在边；交换机按具体 port_id 匹配真实端口。"""
    inst = lut.get(uid)
    if inst is None:
        return None
    if inst.category == "SWITCH" or port_id == "SW":
        # 已分配具体端口时优先按 ID 命中
        if port_id != "SW":
            p = _port(inst, port_id)
            if p:
                return (p.x, p.y, p.side)
        # 兜底：找第一个可用 Dante 端口
        for p in inst.ports:
            if p.signal == Signal.DANTE and not p.air:
                return (p.x, p.y, p.side)
        return (inst.x + inst.w / 2, inst.y, "top")
    p = _port(inst, port_id)
    return (p.x, p.y, p.side) if p else None


# 端口出线最短距离：从模块边缘向外垂直延伸，保证不压边框
_STUB_LEN = 10.0


def _stub_dir(side):
    if side == "left":
        return (-1, 0)
    if side == "right":
        return (1, 0)
    if side == "top":
        return (0, -1)
    if side == "bottom":
        return (0, 1)
    return (1, 0)


def _ortho(x1, y1, x2, y2, first):
    if first == "h":
        mx = (x1 + x2) / 2
        return [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
    my = (y1 + y2) / 2
    return [(x1, y1), (x1, my), (x2, my), (x2, y2)]


def _switch_path(x1, y1, x2, y2, cx):
    """网络线经交换机：走交换机中心 x 的垂直总线，保证规整。"""
    return [(x1, y1), (cx, y1), (cx, y2), (x2, y2)]


class _N:
    category = None


# 网络/控制信号：需避让模块；交叉处按标准制图做「跳线」（拱起小半圆）
NET_SIGNALS = {Signal.DANTE, Signal.IP, Signal.RS232, Signal.GPIO}


# Dante 总线式布线参数
_BUS_MARGIN_Y = 22.0       # 主线在交换机顶部上方多少 px
_BUS_ROLE_GAP = 22.0        # 主/备网络主线垂直间距
_BUS_ROLE_SPACING = 12.0    # 主/备纵向 drop 线水平间距
_BUS_DROP_LEAD = 4.0        # 备份 drop 超出 stub 的最小水平延伸
_BUS_TRUNK_EXT = 12.0      # 主线两端超出最远 drop 的长度
_BUS_TRUNK_WIDTH = 2.4      # 主线线宽
# 分列主干线（单列超 20 个模块拆多列时用）：中性灰蓝，避免与信号线配色混淆
TRUNK_COLOR = "#8b9bb4"
TRUNK_WIDTH = _BUS_TRUNK_WIDTH
_BUS_DROP_WIDTH = 1.6      # drop 线线宽


def _is_switch(lut, uid):
    inst = lut.get(uid)
    return inst is not None and inst.category == "SWITCH"


def _dante_to_switch(c, lut):
    """返回该 Dante 连接所连接的交换机 uid；若不是到交换机的连接则返回 None。"""
    if c.signal != Signal.DANTE:
        return None
    fs = _is_switch(lut, c.from_uid)
    ts = _is_switch(lut, c.to_uid)
    if fs and ts:
        return None  # 交换机到交换机用旧逻辑
    if fs and not ts:
        return c.from_uid
    if ts and not fs:
        return c.to_uid
    return None


def _device_switch_endpoints(c, lut):
    """返回 (设备端点, 交换机端点) 的 3 元组；若不存在则返回 None。"""
    s = _endpoint(lut, c.from_uid, c.from_port)
    t = _endpoint(lut, c.to_uid, c.to_port)
    if not s or not t:
        return None
    if _is_switch(lut, c.from_uid):
        s, t = t, s
    return s, t


def _drop_offset(role: str, side: str) -> float:
    """主/备 drop 水平偏移：两根线都朝远离设备的方向伸出，
    primary 更靠外，backup 略靠内，保证垂直段不重合且都不回穿模块。"""
    is_primary = role == "primary"
    off = _BUS_ROLE_SPACING + _BUS_DROP_LEAD if is_primary else _BUS_DROP_LEAD
    if side == "left":
        return -off
    if side == "right":
        return +off
    # 上下端口兜底：primary 偏左，backup 偏右
    return -off if is_primary else +off


def _draw_dante_bus(canvas, project, dante_conns, lut,
                    label_all=True, rects_all=None, placed=None):
    """Dante 网络线采用底部总线式画法：
    每条交换机对应一条水平主线在最下方，设备网口出线后先往下接到主线，
    主线再进交换机。网络线最后生成，因此会覆盖在其他连线之上。
    多交换机时，主/备主线按 role 全局岔开（一上一下），避免重叠。

    同列堆叠设备：每根 drop 都从设备端口的 stub 点垂直落到主线，
    最上面的设备离主线最远（出线最长），越往下越短；不水平扇开，
    避免底部 drop 互相交叉或"超过对应垂直线"。

    主/备 drop 按端口所在边做水平偏移（都朝远离设备方向），
    使不同类型/角色的垂直线保持可见间距，避免完全重合。
    """
    from collections import defaultdict
    by_switch = defaultdict(list)
    for c in dante_conns:
        sw_uid = _dante_to_switch(c, lut)
        if sw_uid:
            by_switch[sw_uid].append(c)

    if not by_switch:
        return

    rects_all = rects_all if rects_all is not None else _rects_for(project, set())
    placed = placed if placed is not None else []

    # 主/备主线使用全局统一的 y，确保同一 role 的交换机共享一条水平线，
    # 不同 role 的线一上一下岔开。
    sw_top_y = min(lut[uid].y for uid in by_switch)
    role_y = {
        "primary": sw_top_y - _BUS_MARGIN_Y - _BUS_ROLE_GAP,  # 主线靠上
        "backup": sw_top_y - _BUS_MARGIN_Y,                   # 备线靠下
    }

    for sw_uid, conns in by_switch.items():
        sw = lut.get(sw_uid)
        if not sw:
            continue
        by_role = defaultdict(list)
        for c in conns:
            by_role[c.role].append(c)

        for role, rconns in by_role.items():
            bus_y = role_y.get(role, sw_top_y - _BUS_MARGIN_Y)
            color = signal_color(Signal.DANTE, role)
            ltype = signal_ltype(Signal.DANTE, role)

            endpoints = []
            for c in rconns:
                ends = _device_switch_endpoints(c, lut)
                if not ends:
                    continue
                s, t = ends
                sx, sy, sside = s
                tx, ty, tside = t
                sdx, sdy = _stub_dir(sside)
                stub_x = sx + sdx * _STUB_LEN
                stub_y = sy + sdy * _STUB_LEN
                endpoints.append({
                    "c": c, "s": s, "t": t,
                    "stub_x": stub_x, "stub_y": stub_y,
                })
            if not endpoints:
                continue

            # drop 的落点 x：按 role 与端口所在边做水平偏移，
            # 主/备两根 drop 都朝远离设备的方向伸出，保持可见间距，
            # 偏移段位于模块外的 stub 上，不会在模块顶部形成「出头」。
            for e in endpoints:
                e["drop_x"] = e["stub_x"] + _drop_offset(role, e["s"][2])
            all_x = [e["drop_x"] for e in endpoints] + [e["t"][0] for e in endpoints]
            min_x = min(all_x) - _BUS_TRUNK_EXT
            max_x = max(all_x) + _BUS_TRUNK_EXT

            # 1) 先画粗主线
            canvas.add(Polyline(
                points=[(min_x, bus_y), (max_x, bus_y)],
                layer="WIRES_DANTE", color=color, width=_BUS_TRUNK_WIDTH,
                ltype=ltype, tag=f"dante-trunk-{sw.uid}-{role}"))
            if label_all:
                txt = f"DANTE 主干 x{len(endpoints)}"
                pos = _pick_label_pos(txt, [(min_x, bus_y), (max_x, bus_y)],
                                      rects_all, placed)
                if pos:
                    cx, cy, anchor, box = pos
                    placed.append(box)
                    canvas.add(Text(x=cx, y=cy, text=txt, layer="WIRE_LABELS",
                                    color=color, size=7, anchor=anchor))

            # 2) 再画各设备 drop：端口 -> stub -> 水平偏移 -> 垂直落到主线 -> 交换机端口
            for e in endpoints:
                c = e["c"]
                sx, sy, _ = e["s"]
                tx, ty, _ = e["t"]
                pts = [
                    (sx, sy),
                    (e["stub_x"], e["stub_y"]),
                    (e["drop_x"], e["stub_y"]),
                    (e["drop_x"], bus_y),
                    (tx, bus_y),
                    (tx, ty),
                ]
                canvas.add(Polyline(
                    points=pts, layer="WIRES_DANTE", color=color,
                    width=_BUS_DROP_WIDTH, ltype=ltype,
                    tag=f"{c.from_uid}->{c.to_uid}"))
                if label_all:
                    txt = _wire_label(c)
                    pos = _pick_label_pos(txt, pts, rects_all, placed)
                    if pos:
                        cx, cy, anchor, box = pos
                        placed.append(box)
                        canvas.add(Text(
                            x=cx, y=cy, text=txt, layer="WIRE_LABELS",
                            color=color, size=7, anchor=anchor))
# 优先级越高越「连续」，低优先级线在交叉处做跳线
_SIG_PRIORITY = {
    Signal.XLR: 5, Signal.AES: 5, Signal.SPEAKER: 5, Signal.RF: 4, Signal.OPTICAL: 4,
    Signal.DANTE: 3, Signal.IP: 2, Signal.RS232: 2, Signal.GPIO: 2, Signal.POWER: 1,
}
_HOP_H = 6.0       # 跳线拱高
_HOP_GAP = 5.0      # 交点两侧留白


def _segments(points):
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def _seg_intersection(p1, p2, p3, p4):
    x1, y1 = p1; x2, y2 = p2; x3, y3 = p3; x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if den == 0:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if 0 < t < 1 and 0 < u < 1:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def _rects_for(project, skip):
    out = []
    for inst in project.instances + project.switches:
        if inst.uid in skip:
            continue
        out.append((inst.x, inst.y, inst.w, inst.h))
    return out


def _same_col(lut, a, b):
    ia, ib = lut.get(a), lut.get(b)
    if ia is None or ib is None:
        return False
    return ia.x < ib.x + ib.w and ib.x < ia.x + ia.w


def _seg_hits_rect(x1, y1, x2, y2, r):
    rx, ry, rw, rh = r

    def inside(px, py):
        return rx <= px <= rx + rw and ry <= py <= ry + rh

    if inside(x1, y1) or inside(x2, y2):
        return True
    for (ex1, ey1, ex2, ey2) in [(rx, ry, rx + rw, ry), (rx + rw, ry, rx + rw, ry + rh),
                                 (rx + rw, ry + rh, rx, ry + rh), (rx, ry + rh, rx, ry)]:
        if _seg_intersection((x1, y1), (x2, y2), (ex1, ey1), (ex2, ey2)):
            return True
    return False


def _simplify(path):
    """合并共线点，减少 A* 折线冗余。"""
    if len(path) < 3:
        return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        x0, y0 = out[-1]
        x1, y1 = path[i]
        x2, y2 = path[i + 1]
        if not (x0 == x1 == x2 or y0 == y1 == y2):
            out.append(path[i])
    out.append(path[-1])
    return out


def _orthogonalize(path):
    """把任意斜线段拆成直角拐点，保证全正交（仅在出现斜线时插入拐点）。"""
    if len(path) < 2:
        return path
    out = [path[0]]
    for i in range(1, len(path)):
        x0, y0 = out[-1]
        x1, y1 = path[i]
        if abs(x0 - x1) > 1e-6 and abs(y0 - y1) > 1e-6:
            out.append((x1, y0))
        out.append((x1, y1))
    return out


def _astar_route(s, t, rects):
    """正交 A* 布线：将所有模块矩形当障碍绕行（处理同列主备 / 密集场景下 offset 无解）。"""
    try:
        import heapq
    except Exception:
        return None
    INSET2 = 1.0
    pad = 30
    pts = [s, t]
    for (rx, ry, rw, rh) in rects:
        pts.append((rx, ry))
        pts.append((rx + rw, ry + rh))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad
    step = 8
    ncols = int((maxx - minx) // step) + 1
    nrows = int((maxy - miny) // step) + 1

    def cell(x, y):
        return (int((x - minx) // step), int((y - miny) // step))

    def center(c):
        return (minx + (c[0] + 0.5) * step, miny + (c[1] + 0.5) * step)

    blocked = set()
    for (rx, ry, rw, rh) in rects:
        x0, y0 = cell(rx + INSET2, ry + INSET2)
        x1, y1 = cell(rx + rw - INSET2, ry + rh - INSET2)
        for cx in range(max(0, x0), min(ncols - 1, x1) + 1):
            for cy in range(max(0, y0), min(nrows - 1, y1) + 1):
                blocked.add((cx, cy))
    sc = cell(s[0], s[1])
    tc = cell(t[0], t[1])
    blocked.discard(sc)
    blocked.discard(tc)

    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    came = {}
    g = {sc: 0}
    turn = {sc: None}
    pq = [(0, sc)]
    found = False
    while pq:
        f, cur = heapq.heappop(pq)
        if cur == tc:
            found = True
            break
        cx, cy = cur
        for d in dirs:
            nx, ny = cx + d[0], cy + d[1]
            if 0 <= nx < ncols and 0 <= ny < nrows and (nx, ny) not in blocked:
                ng = g[cur] + 1 + (0 if turn.get(cur) == d else 2)
                if ng < g.get((nx, ny), 1e9):
                    g[(nx, ny)] = ng
                    turn[(nx, ny)] = d
                    came[(nx, ny)] = cur
                    h = abs(nx - tc[0]) + abs(ny - tc[1])
                    heapq.heappush(pq, (ng + h, (nx, ny)))
    if not found and tc != sc:
        return None
    path = []
    c = tc
    while c != sc:
        if c not in came:
            return None
        path.append(center(c))
        c = came[c]
    path.append(center(sc))
    path.reverse()
    path[0] = s
    path[-1] = t
    return _simplify(_orthogonalize(path))


def _route_avoid(points, rects, first):
    """所有连线避让模块：优先 offset 正交通道；无解时退化为 A* 网格布线。"""
    if len(points) < 2:
        return points
    offsets = [0, 10, -10, 18, -18, 26, -26, 36, -36, 48, -48,
               64, -64, 82, -82, 104, -104, 130, -130, 160, -160,
               200, -200, 260, -260, 340, -340]

    def valid(cand):
        return not any(_seg_hits_rect(*_flatten_seg(a, b), r)
                       for a, b in _segments(cand) for r in rects)

    if first == "h":
        base = points[2][0]
        for off in offsets:
            mx = base + off
            cand = [points[0], (mx, points[0][1]), (mx, points[3][1]), points[3]]
            if valid(cand):
                return cand
        base_y = points[2][1]
        for off in offsets:
            my = base_y + off
            cand = [points[0], (points[0][0], my), (points[3][0], my), points[3]]
            if valid(cand):
                return cand
    else:
        base = points[1][1]
        for off in offsets:
            my = base + off
            cand = [points[0], (points[0][0], my), (points[3][0], my), points[3]]
            if valid(cand):
                return cand
        base_x = points[2][0]
        for off in offsets:
            mx = base_x + off
            cand = [points[0], (mx, points[0][1]), (mx, points[3][1]), points[3]]
            if valid(cand):
                return cand
    # offset 全被挡：A* 绕行
    a = _astar_route(points[0], points[-1], rects)
    if a:
        return a
    return points


def _flatten_seg(a, b):
    return (a[0], a[1], b[0], b[1])


def _bump(points, pt):
    """在交点 pt 处做标准制图交叉跳线：所有线段保持横平竖直。

    用 detour 替换被穿过的第 k 段，并保留原路径的后续节点。
    """
    xi, yi = pt
    for k in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[k], points[k + 1]
        on_h = abs(y1 - y2) < 1e-6 and abs(yi - y1) < 1e-6
        on_v = abs(x1 - x2) < 1e-6 and abs(xi - x1) < 1e-6
        if on_h and (min(x1, x2) - 1e-6 <= xi <= max(x1, x2) + 1e-6):
            # 水平线被垂直线穿过：向上拱起矩形桥
            L = (xi - _HOP_GAP, yi)
            T1 = (xi - _HOP_GAP, yi - _HOP_H)
            T2 = (xi + _HOP_GAP, yi - _HOP_H)
            R = (xi + _HOP_GAP, yi)
            return points[:k + 1] + [L, T1, T2, R] + points[k + 1:]
        if on_v and (min(y1, y2) - 1e-6 <= yi <= max(y1, y2) + 1e-6):
            # 垂直线被水平线穿过：向右拱起矩形桥
            T = (xi, yi - _HOP_GAP)
            R1 = (xi + _HOP_H, yi - _HOP_GAP)
            R2 = (xi + _HOP_H, yi + _HOP_GAP)
            B = (xi, yi + _HOP_GAP)
            return points[:k + 1] + [T, R1, R2, B] + points[k + 1:]
    return points


def _carve_hops(wires):
    for i in range(len(wires)):
        for j in range(i + 1, len(wires)):
            A, B = wires[i], wires[j]
            hit = None
            for a1, a2 in _segments(A["pts"]):
                for b1, b2 in _segments(B["pts"]):
                    pt = _seg_intersection(a1, a2, b1, b2)
                    if pt:
                        hit = pt
                        break
                if hit:
                    break
            if not hit:
                continue
            # 低优先级线做跳线；高优先级保持连续
            hi, lo = (A, B) if A["pri"] >= B["pri"] else (B, A)
            lo["pts"] = _bump(lo["pts"], hit)


# ---------------- 连线线标（避让模块与已有文字） ----------------
def _text_box(text, x, y, size, anchor):
    """估算文字包围盒。SVG/DXF 里 y 是基线，故上边为 y-size*0.8。"""
    w = len(text) * size * 0.60
    h = size * 1.05
    top = y - size * 0.80
    if anchor == "middle":
        return (x - w / 2, top, w, h)
    if anchor == "end":
        return (x - w, top, w, h)
    return (x, top, w, h)


def _box_hit(a, b, pad=1.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + pad <= bx or bx + bw + pad <= ax or
                ay + ah + pad <= by or by + bh + pad <= ay)


def _box_in_rects(box, rects, pad=1.5):
    return any(_box_hit(box, r, pad) for r in rects)


def _pick_label_pos(text, pts, rects, placed, size=7, hug=3.0):
    """线标统一放在「模块右出线」的上方、贴着线。

    选段优先级（自上而下）：
      1) 水平段优先于垂直段；
      2) 越靠近源端（pts 越靠前的段）越优先 —— 即紧挨模块右侧的那段出线；
      3) 出线方向朝右的优先；
      4) 段越长越优先。

    在选中的段上，从「源端」开始沿出线方向按比例滑动，取第一个既不压模块矩形、
    也不压已有线标的落点；水平段首选线上方 hug 像素（左对齐、沿出线方向展开），
    上方被占则改到线下方；垂直段放线段右侧（右侧被占则改左侧）。
    """
    if len(pts) < 2:
        return None

    segs = []
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        L = abs(x2 - x1) + abs(y2 - y1)
        if L < 8:
            continue
        horiz = abs(y2 - y1) < 0.5
        rightward = (x2 - x1) > 0
        key = (0 if horiz else 1, i, 0 if rightward else 1, -L)
        segs.append((key, (x1, y1), (x2, y2), horiz))
    if not segs:
        return None
    segs.sort(key=lambda s: s[0])

    w = len(text) * size * 0.60
    # 沿一段线从源端向目标端滑动的取样比例
    FWD = (0.0, 0.10, 0.20, 0.32, 0.45, 0.58, 0.72, 0.88, 1.0)
    BWD = tuple(reversed(FWD))

    def _try(seg, below, lift=0.0):
        """在 seg 上滑动取样，取第一个不与模块/已放线标冲突的落点。"""
        _k, (x1, y1), (x2, y2), horiz = seg
        if horiz:
            y = (y1 + y2) / 2.0
            left, right = (x1, x2) if x2 >= x1 else (x2, x1)
            span = right - left
            fracs = FWD if x2 >= x1 else BWD   # 朝右出线：源端在左；朝左出线：源端在右
            for fr in fracs:
                x = left + span * fr
                if x + w > right + 1.0:
                    x = max(left, right - w)
                cy = (y + size + 2.0) if below else (y - hug - lift)
                box = _text_box(text, x, cy, size, "start")
                if _box_in_rects(box, rects):
                    continue
                if any(_box_hit(box, p) for p in placed):
                    continue
                return (x, cy, "start", box)
            return None
        x = (x1 + x2) / 2.0
        top, bot = (y1, y2) if y2 >= y1 else (y2, y1)
        fracs = FWD if y2 >= y1 else BWD
        for fr in fracs:
            yy = top + (bot - top) * fr
            for (cx, cy, anc) in ((x + 4.0, yy, "start"),
                                  (x - 4.0, yy, "end")):
                box = _text_box(text, cx, cy, size, anc)
                if _box_in_rects(box, rects):
                    continue
                if any(_box_hit(box, p) for p in placed):
                    continue
                return (cx, cy, anc, box)
        return None

    # 轮次：① 所有段「贴线上方」→ ② 所有段「上方抬高 4.5」→ ③ 所有段「下方」
    # 这样保证「统一在线上方」优先于「换一段线」，不轻易掉到线下方。
    for below, lift in ((False, 0.0), (False, 4.5), (True, 0.0)):
        for seg in segs:
            got = _try(seg, below, lift)
            if got:
                return got

    # 兜底：贴首段上方（允许轻微重叠，保证每条线都有标注）
    x1, y1 = pts[0]
    x2, y2 = pts[1]
    if abs(y2 - y1) < 0.5:
        cx, cy = min(x1, x2), (y1 + y2) / 2.0 - hug
        return (cx, cy, "start", _text_box(text, cx, cy, size, "start"))
    cx, cy = (x1 + x2) / 2.0 + 4.0, (y1 + y2) / 2.0
    return (cx, cy, "start", _text_box(text, cx, cy, size, "start"))


# 线标显示名：枚举值偏长时在此缩短（阳哥规则 2026-08-30：SPEAKER -> SPK）
WIRE_LABEL_ALIAS = {"SPEAKER": "SPK"}


def _wire_label(c):
    """线标文本：多通道写 `NxSIGNAL`，单通道写 `SIGNAL`，与图例信号类型一致。"""
    sig = c.signal.value if hasattr(c.signal, "value") else str(c.signal)
    sig = WIRE_LABEL_ALIAS.get(sig, sig)
    return f"{c.bundle}x{sig}" if getattr(c, "bundle", 1) > 1 else sig


def draw_trunks(canvas, project):
    """主干线：同一级（stage）拆成多列时，在列顶用一条横线串起来。

    阳哥规则 2026-08-30：单列纵向模块不超过 20 个，超出分多列，
    列间用主线链接。这里的主线是**图面表达**——表示这些子列同属一级、
    共享上游信号；实际信号线仍由 draw_wires 逐条画出。
    """
    trunks = (project.meta or {}).get("trunks") or []
    for t in trunks:
        y, top = t["y"], t["top"]
        # 横向主干（画在连线之下，避免压住线标）
        canvas.add(Line(t["x1"], y, t["x2"], y,
                        color=TRUNK_COLOR, width=TRUNK_WIDTH, layer="TRUNK"))
        # 每个子列顶部引下一根短竖线接入主干
        for cx in t["drops"]:
            canvas.add(Line(cx, y, cx, top,
                            color=TRUNK_COLOR, width=TRUNK_WIDTH, layer="TRUNK"))
        # 端点小圆点，便于识别分接位置
        for cx in t["drops"]:
            canvas.add(Circle(cx, y, 3.0, fill=TRUNK_COLOR,
                              color=TRUNK_COLOR, width=0, layer="TRUNK"))


def draw_wires(canvas, project, label_all=True):
    draw_trunks(canvas, project)
    lut = _lut(project)
    wires = []
    dante_conns = []

    for c in project.connections:
        # Dante 到交换机的连接采用底部总线式画法，最后生成
        if _dante_to_switch(c, lut):
            dante_conns.append(c)
            continue

        s = _endpoint(lut, c.from_uid, c.from_port)
        t = _endpoint(lut, c.to_uid, c.to_port)
        if not s or not t:
            continue
        sx, sy, sside = s
        tx, ty, tside = t
        # 1) 从端口向外垂直延伸最短 stub，保证出线不压边框
        sdx, sdy = _stub_dir(sside)
        tdx, tdy = _stub_dir(tside)
        s2 = (sx + sdx * _STUB_LEN, sy + sdy * _STUB_LEN)
        t2 = (tx + tdx * _STUB_LEN, ty + tdy * _STUB_LEN)

        involves_switch = (lut.get(c.from_uid, _N()).category == "SWITCH" or
                           lut.get(c.to_uid, _N()).category == "SWITCH")
        # 网络线走到交换机实际端口 x 的垂线，避免所有线挤到中心
        if involves_switch:
            cx = t2[0] if lut.get(c.to_uid, _N()).category == "SWITCH" else s2[0]
            pts = _switch_path(s2[0], s2[1], t2[0], t2[1], cx)
            first = "v"  # 交换机连接优先走竖直通道，便于绕过中间模块
        else:
            pts = _ortho(s2[0], s2[1], t2[0], t2[1], "h")
            first = "h"
        # 所有连线（含模拟音频 XLR/AES/SPEAKER）均避让模块，避免「线盖住模块」。
        # 默认排除起止设备自身（避免端口贴边误判）；但主备 failover 或同列设备的
        # 连线必须保留起止设备为障碍，否则会从自身机身内部穿过（实际重叠）。
        if c.role == "backup" or _same_col(lut, c.from_uid, c.to_uid):
            rects = _rects_for(project, set())
        else:
            rects = _rects_for(project, {c.from_uid, c.to_uid})
        pts = _route_avoid(pts, rects, first)
        # 把端口本身接回 stub 端点
        pts = [(sx, sy)] + pts + [(tx, ty)]
        wires.append({"c": c, "pts": pts, "pri": _SIG_PRIORITY.get(c.signal, 3)})

    # 交叉处不再做矩形跳线桥：跳线桥在当前密度下会形成明显的「出头」/缺口，
    # 且 Dante 线最后生成并位于最上层，颜色+线型已足够区分交叉关系。
    # _carve_hops(wires)

    # 非 Dante 连线先画；每条线都带线标（颜色/线型与图例信号类型一致）
    rects_all = _rects_for(project, set())
    placed = []
    for w in wires:
        c = w["c"]
        color = signal_color(c.signal, c.role)
        layer = signal_layer(c.signal)
        ltype = signal_ltype(c.signal, c.role)
        canvas.add(Polyline(
            points=w["pts"], layer=layer, color=color,
            width=1.6 if c.role == "primary" else 1.1, ltype=ltype,
            tag=f"{c.from_uid}->{c.to_uid}"))
        if label_all:
            txt = _wire_label(c)
            pos = _pick_label_pos(txt, w["pts"], rects_all, placed)
            if pos:
                cx, cy, anchor, box = pos
                placed.append(box)
                canvas.add(Text(
                    x=cx, y=cy, text=txt, layer="WIRE_LABELS",
                    color=color, size=7, anchor=anchor))

    # Dante 网络线最后生成，在最上层；采用底部总线式画法
    _draw_dante_bus(canvas, project, dante_conns, lut, label_all=label_all,
                    rects_all=rects_all, placed=placed)


# ---------------- 线型说明（图例表，画在图幅底部） ----------------
_SIGNAL_CN = {
    Signal.XLR:     "模拟音频（XLR）",
    Signal.AES:     "数字音频（AES/EBU）",
    Signal.DANTE:   "Dante 网络音频",
    Signal.RF:      "射频 / 天线（RF）",
    Signal.SPEAKER: "扬声器功率线",
    Signal.RS232:   "控制（RS-232）",
    Signal.IP:      "控制 / 网络（IP）",
    Signal.GPIO:    "控制（GPIO）",
    Signal.OPTICAL: "光纤（OPTICAL）",
    Signal.POWER:   "电源",
}
_SIGNAL_ORDER = [Signal.XLR, Signal.AES, Signal.DANTE, Signal.RF, Signal.SPEAKER,
                 Signal.RS232, Signal.IP, Signal.GPIO, Signal.OPTICAL, Signal.POWER]

_LEG_SAMPLE = 30.0     # 线型样例长度
_LEG_ROW_H = 14.0      # 行高
_LEG_TITLE_H = 20.0    # 标题区高度
_LEG_COL_W = 186.0     # 列宽
_LEG_GAP_TOP = 30.0    # 与图幅底部的间距


def draw_wire_legend(canvas, project, title="线型说明"):
    """在图幅底部绘制「线型说明」表。

    只列出本图**实际出现**的 (信号类型, 主用/备用) 组合，
    每条说明左侧画一小段与图中完全一致的线（同色、同线型、同粗细），
    右侧写中文名称；备用链路额外标注「（备用）」。

    用 Line 而非 Polyline 画样例，避免被下游「连线/线标数量一致性」校验脚本误计。
    """
    if not project.connections:
        return

    roles_by_sig = {}
    for c in project.connections:
        sig = c.signal
        role = getattr(c, "role", "primary") or "primary"
        roles_by_sig.setdefault(sig, set()).add(role)

    rows = []
    for sig in _SIGNAL_ORDER:
        if sig not in roles_by_sig:
            continue
        for role in ("primary", "backup"):
            if role in roles_by_sig[sig]:
                rows.append((sig, role))
    # 兜底：配置里没排到的信号类型
    for sig in roles_by_sig:
        if sig in _SIGNAL_ORDER:
            continue
        for role in sorted(roles_by_sig[sig]):
            rows.append((sig, role))
    if not rows:
        return

    minx, _miny, _maxx, maxy = canvas.bounds()
    ncol = 1 if len(rows) <= 8 else 2
    nrow = (len(rows) + ncol - 1) // ncol
    bw = ncol * _LEG_COL_W + 20
    bh = _LEG_TITLE_H + nrow * _LEG_ROW_H + 10
    x0 = minx
    y0 = maxy + _LEG_GAP_TOP

    # 外框
    canvas.add(Rect(x=x0, y=y0, w=bw, h=bh, layer="LEGEND",
                    color="#8a8578", fill="none", width=1.0, tag="legend-block"))
    # 标题
    canvas.add(Text(x=x0 + 10, y=y0 + 14, text=title, layer="LEGEND",
                    color="#f1efe8", size=10, bold=True, tag="legend-title"))

    for i, (sig, role) in enumerate(rows):
        col, r = divmod(i, nrow)
        cx = x0 + 12 + col * _LEG_COL_W
        cy = y0 + _LEG_TITLE_H + 6 + r * _LEG_ROW_H
        color = signal_color(sig, role)
        ltype = signal_ltype(sig, role)
        canvas.add(Line(x1=cx, y1=cy - 3, x2=cx + _LEG_SAMPLE, y2=cy - 3,
                        layer="LEGEND", color=color, width=1.8, ltype=ltype,
                        tag=f"legend-sample-{sig.value}-{role}"))
        name = _SIGNAL_CN.get(sig, sig.value)
        if role == "backup":
            name += "（备用）"
        canvas.add(Text(x=cx + _LEG_SAMPLE + 8, y=cy, text=name, layer="LEGEND",
                        color="#e8e6dd", size=8, tag="legend-text"))
