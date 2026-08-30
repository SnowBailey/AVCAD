"""DXF 文字锚点/居中对齐校验：SVG 与 DXF 应使用同一套 x/y 语义。

改用 MTEXT 输出，因为 AutoCAD 2023/2024 对单行 TEXT 的 BOTTOM_CENTER
对齐存在已知 bug（文字会随机偏移到一侧），MTEXT 的 attachment_point
居中更稳定。"""
import os
import tempfile

import pytest
from ezdxf.lldxf.const import MTEXT_BOTTOM_LEFT, MTEXT_BOTTOM_CENTER, MTEXT_BOTTOM_RIGHT

from avcad.render.dxf_render import render_dxf
from avcad.render.primitives import Canvas, Text


def _ap_of(entity):
    return entity.dxf.attachment_point


def test_dxf_text_anchor_mapping():
    """Text.anchor 必须被转成 MTEXT attachment_point，不能一律当成左下角。"""
    c = Canvas(bg="#111")
    c.add(Text(x=10, y=10, text="start", layer="LABELS", color="#fff", size=4, anchor="start"))
    c.add(Text(x=20, y=20, text="middle", layer="LABELS", color="#fff", size=4, anchor="middle"))
    c.add(Text(x=30, y=30, text="end", layer="LABELS", color="#fff", size=4, anchor="end"))

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
        path = f.name
    try:
        doc = render_dxf(c, path, project_name="_ignore_")
        msp = doc.modelspace()
        mtexts = {t.text: t for t in msp.query('MTEXT[layer=="LABELS"]')
                  if t.text in ("start", "middle", "end")}
        assert len(mtexts) == 3

        assert _ap_of(mtexts["start"]) == MTEXT_BOTTOM_LEFT
        assert _ap_of(mtexts["middle"]) == MTEXT_BOTTOM_CENTER
        assert _ap_of(mtexts["end"]) == MTEXT_BOTTOM_RIGHT

        # DXF y 已翻转；insert.x 保持原 x（MTEXT 的 attachment_point 会处理水平偏移）
        assert mtexts["start"].dxf.insert[0] == pytest.approx(10, abs=1e-3)
        assert mtexts["middle"].dxf.insert[0] == pytest.approx(20, abs=1e-3)
        assert mtexts["end"].dxf.insert[0] == pytest.approx(30, abs=1e-3)
    finally:
        os.unlink(path)
