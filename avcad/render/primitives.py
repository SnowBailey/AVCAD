"""与格式无关的绘制原语 + 画布。SVG/DXF 渲染器消费同一份原语。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Rect:
    x: float; y: float; w: float; h: float
    layer: str = "DEVICES"
    color: str = "#d3d1c7"
    fill: str = "none"
    width: float = 1.0
    dash: Optional[str] = None
    tag: str = ""


@dataclass
class Line:
    x1: float; y1: float; x2: float; y2: float
    layer: str = "WIRES"
    color: str = "#cccccc"
    width: float = 1.0
    ltype: str = "solid"   # solid / dashed / dotted
    tag: str = ""


@dataclass
class Polyline:
    points: list          # [(x,y),...]
    layer: str = "WIRES"
    color: str = "#cccccc"
    width: float = 1.0
    ltype: str = "solid"
    tag: str = ""


@dataclass
class Text:
    x: float; y: float
    text: str
    layer: str = "LABELS"
    color: str = "#f1efe8"
    size: float = 9
    anchor: str = "start"   # start / middle / end
    bold: bool = False
    tag: str = ""


@dataclass
class Circle:
    x: float; y: float; r: float
    layer: str = "DEVICES"
    color: str = "#d3d1c7"
    fill: str = "none"
    width: float = 1.0
    tag: str = ""


@dataclass
class Port:
    x: float; y: float; r: float = 2.4
    color: str = "#d3d1c7"
    layer: str = "PORTS"
    tag: str = ""


@dataclass
class Canvas:
    primitives: list = field(default_factory=list)
    width: float = 1000.0
    height: float = 600.0
    bg: str = "#1e1e1c"

    def add(self, *items):
        self.primitives.extend(items)
        return self

    def bounds(self):
        xs, ys = [], []
        for p in self.primitives:
            if isinstance(p, (Rect, Text, Circle)):
                xs.append(p.x); ys.append(p.y)
            if isinstance(p, Rect):
                xs.append(p.x + p.w); ys.append(p.y + p.h)
            if isinstance(p, Line):
                xs += [p.x1, p.x2]; ys += [p.y1, p.y2]
            if isinstance(p, Polyline):
                for px, py in p.points:
                    xs.append(px); ys.append(py)
            if isinstance(p, Port):
                xs.append(p.x); ys.append(p.y)
        if not xs:
            return 0, 0, self.width, self.height
        return min(xs), min(ys), max(xs), max(ys)
