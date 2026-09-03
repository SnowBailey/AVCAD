"""核心数据模型：信号域、端口、设备规格、设备实例、连线、工程对象。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class Signal(str, Enum):
    XLR = "XLR"          # 模拟音频
    AES = "AES"          # 数字音频 AES3
    DANTE = "DANTE"      # 网络音频（属 AUDIO 域）
    RS232 = "RS232"      # 控制
    IP = "IP"            # 控制/网络
    GPIO = "GPIO"        # 控制
    RF = "RF"            # 天线射频
    SPEAKER = "SPEAKER"  # 扬声器线缆
    POWER = "POWER"      # 电源
    OPTICAL = "OPTICAL"  # 光纤
    TRS = "TRS"          # 模拟音频 6.35mm（非平衡/混合输出）
    CONF = "CONF"        # 会议专用线（六芯主缆 / T 型线，手拉手）
    USB = "USB"          # USB 音频接口（录音/播放/免驱声卡）
    LINK = "LINK"        # 设备间级联/扩展总线（如自动混音器级联口）
    WCLK = "WCLK"        # 字时钟（Word Clock）数字音频同步基准，BNC 接口


class Domain(str, Enum):
    AUDIO = "AUDIO"
    CONTROL = "CONTROL"
    POWER = "POWER"
    RF = "RF"


SIGNAL_DOMAIN = {
    Signal.XLR: Domain.AUDIO, Signal.AES: Domain.AUDIO, Signal.DANTE: Domain.AUDIO,
    Signal.RS232: Domain.CONTROL, Signal.IP: Domain.CONTROL, Signal.GPIO: Domain.CONTROL,
    Signal.RF: Domain.RF, Signal.SPEAKER: Domain.AUDIO,
    Signal.POWER: Domain.POWER,     Signal.OPTICAL: Domain.AUDIO,
    Signal.TRS: Domain.AUDIO,
    Signal.CONF: Domain.AUDIO,
    Signal.USB: Domain.AUDIO,
    Signal.LINK: Domain.AUDIO,
    Signal.WCLK: Domain.AUDIO,
}

# 信号配色（SVG hex）与 DXF 图层/线型映射；role 决定主/备线型。
# 这是内置默认 fallback；实际配色由 avcad/config/signal_colors.json 驱动
# （每种信号分别定义 primary / backup 的 color / layer / ltype），
# 便于在清单确认环节点击更改并持久化，拓扑(SVG)与 CAD(DXF) 共用同一份配置。
SIGNAL_META = {
    Signal.XLR:     dict(color="#5dcaa5", dxf_color=4,  layer="WIRES_ANALOG",  ltype="solid"),
    Signal.AES:     dict(color="#3aa6a0", dxf_color=4,  layer="WIRES_DIGITAL", ltype="solid"),
    Signal.DANTE:   dict(color="#378add", dxf_color=5,  layer="WIRES_DANTE",   ltype="solid"),
    Signal.RS232:   dict(color="#b07cd9", dxf_color=6,  layer="WIRES_CONTROL", ltype="dotted"),
    Signal.IP:      dict(color="#b07cd9", dxf_color=6,  layer="WIRES_CONTROL", ltype="dotted"),
    Signal.GPIO:    dict(color="#b07cd9", dxf_color=6,  layer="WIRES_CONTROL", ltype="dotted"),
    Signal.RF:      dict(color="#e8923c", dxf_color=40, layer="WIRES_RF",      ltype="solid"),
    Signal.SPEAKER: dict(color="#e8655a", dxf_color=1,  layer="WIRES_SPEAKER", ltype="solid"),
    Signal.POWER:   dict(color="#cfcfcf", dxf_color=7,  layer="WIRES_POWER",   ltype="solid"),
    Signal.OPTICAL: dict(color="#7fd1e8", dxf_color=4,  layer="WIRES_DIGITAL", ltype="solid"),
    Signal.TRS:     dict(color="#7fbf9a", dxf_color=4,  layer="WIRES_ANALOG",  ltype="solid"),
    Signal.CONF:    dict(color="#c9a227", dxf_color=2,  layer="WIRES_CONF",    ltype="solid"),
    Signal.USB:     dict(color="#8f7ae6", dxf_color=6,  layer="WIRES_USB",     ltype="dotted"),
    Signal.LINK:    dict(color="#8a93a8", dxf_color=6,  layer="WIRES_LINK",    ltype="solid"),
    Signal.WCLK:    dict(color="#d65db1", dxf_color=215, layer="WIRES_WCLK",   ltype="solid"),
}

# 配色配置文件路径（全局共享，跨项目复用；用户可在清单确认环节点击更改）
_COLOR_CFG_PATH = Path(__file__).resolve().parents[1] / "config" / "signal_colors.json"
_COLOR_CFG = None


def _load_color_cfg():
    """加载 signal_colors.json；文件缺失/损坏时回退到内置 SIGNAL_META 推导。"""
    global _COLOR_CFG
    if _COLOR_CFG is not None:
        return _COLOR_CFG
    data = None
    try:
        data = json.loads(_COLOR_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = None
    if not data:
        data = {}
        for sig, meta in SIGNAL_META.items():
            data[sig.value] = {
                "primary": {"color": meta["color"], "layer": meta["layer"], "ltype": meta["ltype"]},
                "backup": {"color": meta["color"], "layer": meta["layer"], "ltype": "dashed"},
            }
    _COLOR_CFG = data
    return _COLOR_CFG


def reload_color_config():
    """强制重新读取配置文件（server 改完 JSON 后调用，确保进程内生效）。"""
    global _COLOR_CFG
    _COLOR_CFG = None
    return _load_color_cfg()


def _signal_entry(sig):
    cfg = _load_color_cfg()
    key = sig.value if isinstance(sig, Signal) else str(sig)
    return cfg.get(key, {})


class Redundancy(str, Enum):
    NONE = "NONE"
    DEVICE_BACKUP = "DEVICE_BACKUP"
    PROCESSOR_BACKUP = "PROCESSOR_BACKUP"
    LINK_BACKUP = "LINK_BACKUP"
    FULL_CHAIN = "FULL_CHAIN"


# 冗余级别 → 行为定义。★ 三档（五档）冗余的**唯一权威**，调用点禁止各写一份 mapping。
#   categories    : 需复制成主备的设备类别（SWITCH 不在此列，走 _make_switches）
#   dual_switch   : 是否强制生成双交换机（链路冗余的物理体现）
#   failover_link : 是否画「主 → 备」音频 failover 线
#
# 语义（2026-08-31 阳哥要求「你来判断最佳方案」后定）：
#   DEVICE_BACKUP    调音台等**设备级**热备 —— 画主备直连音频线，单链路即可
#   PROCESSOR_BACKUP 处理器热备 —— 处理器是系统核心，同样画主备直连线
#   LINK_BACKUP      **链路**冗余 —— 冗余在网络层（Dante 主备网），冗余载体是
#                    交换机本身；主备各走一台交换机，**不画**设备间 failover 线
#   FULL_CHAIN       全链路 —— 设备级 + 链路级都要
REDUNDANCY_SCOPE = {
    "DEVICE_BACKUP": {
        "categories": ("MIXER",),
        "dual_switch": False,
        "failover_link": True,
    },
    "PROCESSOR_BACKUP": {
        "categories": ("PROCESSOR",),
        "dual_switch": False,
        "failover_link": True,
    },
    "LINK_BACKUP": {
        "categories": ("SWITCH",),
        "dual_switch": True,
        "failover_link": False,
    },
    "FULL_CHAIN": {
        "categories": ("MIXER", "PROCESSOR", "SWITCH"),
        "dual_switch": True,
        "failover_link": True,
    },
}

_EMPTY_SCOPE = {"categories": (), "dual_switch": False, "failover_link": False}


def redundancy_scope(level) -> dict:
    """取冗余级别的行为定义；NONE / 未知值返回空定义（不复制、不双交换机、不画线）。"""
    key = level.value if isinstance(level, Redundancy) else str(level or "NONE")
    return REDUNDANCY_SCOPE.get(key, _EMPTY_SCOPE)


def redundancy_levels() -> tuple:
    """所有非 NONE 的冗余级别（供清单解析、UI 下拉、校验提示复用）。"""
    return tuple(REDUNDANCY_SCOPE)


# 清单里「冗余」列的中文写法 → 枚举。★ 该列的列名别名里就有「冗余」「主备」，
# 中文用户自然会写中文值；而 `Redundancy(str(v).upper())` 遇到中文会直接
# ValueError 崩掉整张清单，所以必须归一化。裸写「主备」= 设备级热备（最常见）。
_REDUNDANCY_ALIASES = {
    "无": "NONE", "无冗余": "NONE", "不冗余": "NONE", "单链路": "NONE",
    "主备": "DEVICE_BACKUP", "设备主备": "DEVICE_BACKUP", "设备冗余": "DEVICE_BACKUP",
    "设备级热备": "DEVICE_BACKUP", "调音台主备": "DEVICE_BACKUP",
    "处理器主备": "PROCESSOR_BACKUP", "处理器冗余": "PROCESSOR_BACKUP",
    "处理器热备": "PROCESSOR_BACKUP",
    "链路主备": "LINK_BACKUP", "链路冗余": "LINK_BACKUP", "双链路": "LINK_BACKUP",
    "双网": "LINK_BACKUP", "双交换机": "LINK_BACKUP",
    "全链路": "FULL_CHAIN", "全链路主备": "FULL_CHAIN", "全冗余": "FULL_CHAIN",
}

_REDUNDANCY_ALIAS_FLAT = {
    k.replace(" ", "").replace("_", "").replace("-", "").upper(): v
    for k, v in _REDUNDANCY_ALIASES.items()
}

# 去下划线后的枚举名 → 原枚举名（容忍 "processor backup" / "fullchain" 这类写法）
_REDUNDANCY_ENUM_FLAT = {k.replace("_", ""): k for k in Redundancy.__members__}


def normalize_redundancy(val) -> Redundancy:
    """把清单里的冗余写法（含中文）归一到 Redundancy 枚举。

    无法识别的写法一律降级为 NONE 而不是抛异常——宁可「没设冗余」，
    也不能让一行写错就崩掉整张清单。
    """
    if isinstance(val, Redundancy):
        return val
    raw = str(val or "").strip().upper()
    if not raw:
        return Redundancy.NONE
    # ★ 先按原样匹配枚举名（FULL_CHAIN），再去空格/下划线做模糊匹配
    #   （"full chain" / "fullchain"）。顺序反了会让正常英文值全部降级成 NONE。
    if raw in Redundancy.__members__:
        return Redundancy[raw]
    flat = raw.replace(" ", "").replace("_", "").replace("-", "")
    if flat in _REDUNDANCY_ENUM_FLAT:
        return Redundancy[_REDUNDANCY_ENUM_FLAT[flat]]
    lvl = _REDUNDANCY_ALIAS_FLAT.get(flat)
    return Redundancy(lvl) if lvl else Redundancy.NONE


# 端口方向的唯一权威取值。前端 `index.html` 的 SIDES / ROLES 常量必须与之
# 一致（由 avcad/tests/test_port_geometry.py 守卫）；写错不会崩，但会静默失效：
#   side 错 -> 布局不认识该方向，端口坐标停在 (0,0)，图纸上直接飞出去
#   role 错 -> 端口不参与任何进出配对，图上表现为「余量未连」，极难察觉
VALID_SIDES = ("left", "right", "top", "bottom")
VALID_ROLES = ("in", "out", "io")     # io = 双向，不参与进出配对


@dataclass
class Port:
    id: str
    # side 取值必须与前端 SIDES 常量一致（avcad/tests/test_port_geometry.py 守卫）
    side: str            # left / right / top / bottom
    signal: Signal
    label: str
    count: int = 1
    role: str = "io"     # in / out / io（io = 双向，不参与进出配对）
    air: bool = False    # True = 空中/非线缆接口（天线 RF 收发），不连线、不校验未连


@dataclass
class ConcretePort:
    """展开后的具体端口，布局阶段会填入坐标。"""
    id: str
    uid: str
    side: str
    signal: Signal
    label: str
    index: int = 0           # 同侧第几个
    x: float = 0.0
    y: float = 0.0
    role: str = "io"
    air: bool = False     # True = 空中/非线缆接口（如天线 RF 收发），不连线、不校验未连


@dataclass
class DeviceSpec:
    category: str
    name: str
    redundancy_allowed: bool = False
    proc_func: Optional[str] = None     # automix / system
    features_available: list = field(default_factory=list)
    params: dict = field(default_factory=dict)   # 参数定义 {key:{min,default,label,unit}}
    ports_template: list = field(default_factory=list)
    electrical: dict = field(default_factory=dict)
    fixed: dict = field(default_factory=dict)


@dataclass
class DeviceInstance:
    uid: str
    category: str
    name: str
    brand: str = ""
    model: str = ""
    quantity: int = 1
    features: set = field(default_factory=set)
    params: dict = field(default_factory=dict)
    # 电气属性（可选量化键，用于 GB 55024-2022 强条 ERROR 级判定）：
    #   ground_wire_mm2 接地/等电位联结导体截面积(mm²)，强条要求 ≥4；
    #   emg_spl_db 应急广播设计声压级(dB)；bg_spl_db 背景噪声声压级(dB)。
    # 缺这些键则对应强条退回 WARN 提醒，不阻断。
    electrical: dict = field(default_factory=dict)
    ports: list = field(default_factory=list)   # List[ConcretePort]
    redundancy: Redundancy = Redundancy.NONE
    pair: Optional[str] = None
    active: bool = False            # 有源扬声器
    spec_ref: str = ""
    is_backup: bool = False         # 主备对中备用成员
    # 可扩展卡槽（如 YAMAHA HY/MY/RY 插槽），非对外接口，仅可视化
    slots: list = field(default_factory=list)
    # 布局
    x: float = 0.0
    y: float = 0.0
    w: float = 90.0
    h: float = 60.0
    stage: str = ""
    redundant_group: Optional[str] = None
    # EASE/MAPP 对接：扬声器空间姿态（单位：度，高度/长度单位：米）。
    # 默认 0.0 = 地面层、正对前方水平、无旋转。仅在出 EASE 包时读取。
    z: float = 0.0           # 安装高度（米），默认 0（地面层）
    aim_az: float = 0.0     # 水平指向角 azimuth（度）
    aim_el: float = 0.0     # 俯仰指向角 elevation（度）
    rot_z: float = 0.0      # 绕 Z 轴安装旋转（度）


@dataclass
class Connection:
    from_uid: str
    from_port: str
    to_uid: str
    to_port: str
    signal: Signal
    role: str = "primary"      # primary / backup
    bundle: int = 1
    note: str = ""


@dataclass
class Issue:
    level: str          # ERROR / WARN / INFO
    code: str
    msg: str
    ref: str = ""


@dataclass
class Project:
    name: str = "AV System"
    instances: list = field(default_factory=list)
    chain: list = field(default_factory=list)        # 有序阶段（category 列表）
    connections: list = field(default_factory=list)
    switches: list = field(default_factory=list)     # Dante 交换机实例
    issues: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def domain_of(sig: Signal) -> Domain:
    return SIGNAL_DOMAIN.get(sig, Domain.AUDIO)


def signal_color(sig: Signal, role: str = "primary") -> str:
    entry = _signal_entry(sig)
    if entry:
        m = entry.get(role) or entry.get("primary") or {}
        return m.get("color", SIGNAL_META[sig]["color"])
    return SIGNAL_META[sig]["color"]


def signal_layer(sig: Signal) -> str:
    entry = _signal_entry(sig)
    if entry:
        m = entry.get("primary") or {}
        return m.get("layer", SIGNAL_META[sig]["layer"])
    return SIGNAL_META[sig]["layer"]


def signal_ltype(sig: Signal, role: str = "primary") -> str:
    entry = _signal_entry(sig)
    if entry:
        m = entry.get(role) or entry.get("primary") or {}
        return m.get("ltype", SIGNAL_META[sig]["ltype"])
    return SIGNAL_META[sig]["ltype"]
