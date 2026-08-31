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
    PROCESSOR_BACKUP = "PROCESSOR_BACKUP"
    LINK_BACKUP = "LINK_BACKUP"
    FULL_CHAIN = "FULL_CHAIN"


@dataclass
class Port:
    id: str
    side: str            # left / right / top
    signal: Signal
    label: str
    count: int = 1
    role: str = "io"     # in / out


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
