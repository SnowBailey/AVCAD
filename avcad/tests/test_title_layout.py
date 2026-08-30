"""标题布局回归：标题三行钉在模块顶部标题区（高度 ~32px），不随模块高度做竖直居中。

防止误将标题块改成「上下居中」——那会让文字落在模块竖直中线，
视觉上像「写在模块中间」。水平方向始终 anchor="middle" 左右居中。
"""
from avcad.render.primitives import Canvas
from avcad.render.draw import draw_devices
from avcad.core.build import build_project


def _title_texts(canvas, inst):
    return [t for t in canvas.primitives
            if t.__class__.__name__ == "Text" and t.tag == inst.uid]


def test_title_stays_at_top_not_vertically_centered():
    proj = build_project([{"category": "MIXER", "name": "数字调音台",
                           "brand": "ALLEN&HEATH", "model": "QU-16", "quantity": 1}],
                          name="T")
    inst = next(i for i in proj.instances if i.category == "MIXER")
    canvas = Canvas()
    draw_devices(canvas, proj)

    titles = _title_texts(canvas, inst)
    assert titles, "应有标题文本"
    # 模块高度通常较大（>32），竖直中心远在标题区之下
    vcenter = inst.y + inst.h / 2
    for t in titles:
        # 标题必须靠近模块顶部（< 标题区 35px），而非竖直中心
        assert (t.y - inst.y) < 35, \
            f"标题 y={t.y:.1f} 距顶 {t.y-inst.y:.1f}，疑似被竖直居中(模块中心={vcenter:.1f})"
        assert t.anchor == "middle", "标题必须左右居中"


def test_title_horizontal_centered_on_module():
    proj = build_project([{"category": "AMP", "name": "功放", "model": "X4",
                           "brand": "IPS", "quantity": 1,
                           "electrical": {"min_load_ohm": 4, "power_w_per_ch": 900}}],
                          name="T")
    inst = next(i for i in proj.instances if i.category == "AMP")
    canvas = Canvas()
    draw_devices(canvas, proj)
    cx = inst.x + inst.w / 2
    for t in _title_texts(canvas, inst):
        assert abs(t.x - cx) < 0.5, f"标题 x={t.x:.1f} 未落在模块中心 {cx:.1f}（未左右居中）"
        assert t.anchor == "middle"
