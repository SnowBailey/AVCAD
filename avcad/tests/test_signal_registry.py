"""信号类型「四处名单」的一致性守卫。

背景（2026-08-31）：`RS232` 在后端枚举、配色表里都齐全，唯独漏在前端
`ui/static/index.html` 的 `SIGNALS` 常量里。后果比「下拉少一个选项」严重得多——
第③步图例确认页渲染端口下拉用的是：

    const sel = (arr, val)=>arr.map(o=>'<option' + (o === val ? " selected" : "") ...

**值不在候选列表里时没有任何 option 被选中**，而 `<select>` 会默认选中第一项
（XLR）。用户哪怕只改了端口标签，`sync()` 就把 `select.value` 写回 `p.signal`，
RS232 静默变成 XLR。图例库里已有 4 个设备（EM20D / EM30D / EM50Q / GMN1208D）
的 4 个 RS232 端口处在危险中。

同一轮还发现 `render/draw._SIGNAL_CN` 漏了 CONF / TRS / USB / LINK——1F 会议室
真实出图有 10 条 CONF 连线，图幅底部的线型说明表只能显示英文原名。

一个信号类型散在 4 处维护，靠人肉同步必然再漏。这个文件把「枚举里有的信号，
另外 3 处都必须有」变成自动断言。

新增信号时必须同步 4 处：
  1. avcad/model/schema.py        —— Signal 枚举 + SIGNAL_META
  2. avcad/config/signal_colors.json —— 配色（主/备）
  3. avcad/ui/static/index.html   —— 前端常量 SIGNALS
  4. avcad/render/draw.py         —— _SIGNAL_CN 中文名 + _SIGNAL_ORDER 排序
"""
from __future__ import annotations

import os
import re

import pytest

from avcad.model.schema import Signal, _load_color_cfg
from avcad.render.draw import _SIGNAL_CN, _SIGNAL_ORDER, WIRE_LABEL_ALIAS


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INDEX_HTML = os.path.join(_ROOT, "avcad", "ui", "static", "index.html")


def _enum_values():
    return [s.value for s in Signal]


def _frontend_signals():
    """从 index.html 里抠出 `const SIGNALS = [...]`。缺文件/缺常量都直接失败。"""
    assert os.path.exists(_INDEX_HTML), f"找不到前端文件：{_INDEX_HTML}"
    src = open(_INDEX_HTML, encoding="utf-8").read()
    m = re.search(r"const\s+SIGNALS\s*=\s*\[(.*?)\]\s*;", src, re.S)
    assert m, (
        "index.html 里找不到 `const SIGNALS = [...]`。若常量改名了请同步本守卫，"
        "别直接删掉这条断言——它防的是「前端下拉漏信号导致端口信号被静默改写」。"
    )
    return re.findall(r'"([^"]+)"', m.group(1))


def test_signal_colors_cover_every_signal():
    """配色表必须覆盖枚举里的每个信号，否则画图时取不到颜色。"""
    colors = _load_color_cfg()
    missing = [s for s in _enum_values() if s not in colors]
    extra = [c for c in colors if c not in _enum_values()]
    assert not missing, f"signal_colors.json 缺少配色：{missing}"
    assert not extra, (
        f"signal_colors.json 里有枚举不存在的信号：{extra}。"
        f"（枚举已改？请一并删除配色里的残留项）"
    )


def test_frontend_dropdown_covers_every_signal():
    """★ 前端端口下拉必须覆盖每个信号。

    漏一个的后果不是「少个选项」：select 在没有任何 option 命中当前值时，
    会默认选中第一项，用户一保存就把该端口的信号静默改成第一项的值
    （RS232 曾因此变成 XLR）。
    """
    front = _frontend_signals()
    missing = [s for s in _enum_values() if s not in front]
    extra = [s for s in front if s not in _enum_values()]
    assert not missing, (
        f"index.html 的 SIGNALS 缺少：{missing}。后果：图例确认页编辑这些端口时，"
        f"信号会被静默改写成下拉首项 {front[0]!r}"
    )
    assert not extra, f"index.html 的 SIGNALS 里有枚举不存在的信号：{extra}"
    assert len(front) == len(set(front)), f"SIGNALS 有重复项：{front}"


def test_every_signal_has_chinese_legend_name():
    """每个信号都要有中文名，否则图幅底部的线型说明只显示英文原名。"""
    missing = [s for s in Signal if not _SIGNAL_CN.get(s)]
    assert not missing, (
        f"render/draw.py 的 _SIGNAL_CN 缺少：{[s.value for s in missing]}。"
        f"图幅底部说明表会把它们显示成英文原名（真实清单里 CONF 已中招过）"
    )


def test_legend_order_covers_every_signal():
    """_SIGNAL_ORDER 决定说明表的行序，漏排的信号只能靠兜底逻辑追加到末尾。"""
    missing = [s for s in Signal if s not in _SIGNAL_ORDER]
    extra = [s for s in _SIGNAL_ORDER if s not in list(Signal)]
    assert not missing, (
        f"_SIGNAL_ORDER 缺少：{[s.value for s in missing]}。"
        f"（新增信号时请**追加到末尾**，插队会改变已有图纸的说明表行序）"
    )
    assert not extra, f"_SIGNAL_ORDER 里有枚举不存在的信号：{[s.value for s in extra]}"
    assert len(_SIGNAL_ORDER) == len(set(_SIGNAL_ORDER)), "_SIGNAL_ORDER 有重复项"


def test_wire_label_alias_keys_are_real_signals():
    """线标别名的键必须是真实信号——写错键名等于这个别名永远不生效。"""
    values = {s.value for s in Signal}
    bad = [k for k in WIRE_LABEL_ALIAS if k not in values]
    assert not bad, f"WIRE_LABEL_ALIAS 的键不是合法信号：{bad}"


def test_wire_label_alias_does_not_collide():
    """别名不能撞上另一个真实信号的名——两张图会分不清谁是谁。"""
    values = {s.value for s in Signal}
    bad = [(k, v) for k, v in WIRE_LABEL_ALIAS.items()
           if v in values and v != k]
    assert not bad, (
        f"WIRE_LABEL_ALIAS 的别名与其它信号同名，图上会混淆：{bad}"
    )
