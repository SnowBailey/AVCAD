"""静态守卫生效：index.html 里切页/重跑的 UI 关键约定。

背景（2026-09-04）：阳哥第5步报「标签高亮不切换 + toast 跳第1页」。
挖出两个 root cause：
  ① ``renderActivePage`` 之前只写隐藏的 ``#svg.innerHTML``，从未刷可见的
     ``#preview.innerHTML``（line 533 ``#svg{display:none}``，line 859
     ``$("preview").innerHTML`` 只在 ``updateRightPanel`` 触发，切页时
     不走那里），导致切页 SVG 视觉不换，且 ``.preview``（overflow:auto）
     继承上一页 scrollTop → 看起来"切了看不出"。
  ② ``doGenerate`` 无条件 ``STATE.activePage = 0``；setAnon 在第二页
     点「隐藏品牌型号」会调 ``doGenerate()``，于是当前页号被打回 0。
     改成「只在首次出图重置，重跑（isRerun）保留」。

本测试不依赖 jsdom；只断言源码字符层约束——任何把这两个 fix 改回去的人
跑这条测试会立即红。改文本前先读测试注释理解约束。
"""
from __future__ import annotations
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "avcad" / "ui" / "static" / "index.html"


def _read() -> str:
    assert INDEX.exists(), f"缺前端源：{INDEX}"
    return INDEX.read_text("utf-8")


def _function_body(src: str, name: str) -> str:
    """切出 ``function name(args){...}`` 的体内字符串（粗略括号配对，
    够用于本测试的子串断言；JS 函数体不会有顶层嵌套 function）。"""
    # 匹配形如 ``function name(... ) {`` 起点；粗略取第一个 '}' 闭合
    m = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", src)
    assert m, f"源码里没有 function {name}"
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{": depth += 1
        elif c == "}": depth -= 1
        i += 1
    assert depth == 0, f"function {name} 括号未闭合"
    return src[start:i - 1]


def test_render_active_page_writes_visible_preview():
    """切页必须刷 ``#preview.innerHTML``（真可见图容器，line 127/731），
    不能只写隐藏的 ``#svg``——否则用户看到 SVG 不变。

    注意：必须**只**在 ``renderActivePage`` 函数体内匹——
    ``updateRightPanel`` 第 859 行也有同名字串，全文 grep 会误判通过。
    """
    src = _read()
    body = _function_body(src, "renderActivePage")
    writes_preview = (
        '$("preview").innerHTML = STATE.svg' in body          # 直接写法
        or "prev.innerHTML = STATE.svg" in body              # 先 const prev = $("preview") 再写
        or "prev.innerHTML = pg.svg" in body                 # 多页时不通过 STATE.svg 中转
    )
    assert writes_preview, (
        "renderActivePage 函数体内必须写可见预览容器 #preview.innerHTML = STATE.svg（或 prev.innerHTML = pg.svg）。"
        "#svg 是隐藏 e2e 容器（line 533 #svg{display:none}），切页得另写可见的 #preview，"
        "否则切页后 .preview 不刷内容 + 继承 scrollTop，导致视觉看不出切换"
    )


def test_render_active_page_resets_preview_scroll():
    """切页必须把 ``.preview`` 滚动位置清零（.preview 是 overflow:auto，
    innerHTML 替换不会自动 scrollTop=0）。"""
    src = _read()
    body = _function_body(src, "renderActivePage")
    assert ('prev.scrollTop = 0' in body
            or '$("preview").scrollTop = 0' in body), (
        "renderActivePage 函数体内必须显式把 #preview/.preview scrollTop 重置为 0，"
        "避免 innerHTML 替换继承上一页 scrollTop 造成视觉'切了看不出'"
    )


def test_svg_e2e_container_remains_hidden():
    """#svg 是隐藏的 e2e/数据容器（line 533），不能被改成可见——切页时
    会和 .preview 两处显示同一张图，且 e2e 选择器误读。"""
    src = _read()
    assert "#svg{display:none}" in src, (
        "#svg{display:none} 这条约定没了；切页机制依赖："
        "可见 SVG 在 #preview，e2e/数据容器在 #svg。若改可见，"
        "renderActivePage 的 prev.innerHTML 与 svgEl.innerHTML 会双写并样式冲突"
    )


def test_do_generate_preserves_active_page_on_rerun():
    """doGenerate 必须在重跑（isRerun）时保留当前 activePage，
    否则 setAnon 切匿名会把页面号打回 0。"""
    src = _read()
    body = _function_body(src, "doGenerate")
    assert "isRerun" in body, (
        "doGenerate 缺 isRerun 判定；setAnon 调它会把 STATE.activePage 强制回 0，"
        "已经在第 2 页的用户点「隐藏品牌型号」会跳回第 1 页（阳哥 2026-09-04 实测）"
    )
    assert "if(!isRerun) STATE.activePage = 0" in body, (
        "doGenerate 必须用 'if(!isRun) STATE.activePage = 0' 守护首次重置逻辑；"
        "任何简化（如 STATE.activePage = isRerun ? STATE.activePage : 0）也算合规，"
        "但裸 STATE.activePage = 0 是禁用"
    )


def test_page_tabs_uses_event_delegation():
    """页签必须用事件委托：监听器挂在 #pageTabs 容器，按钮重建不影响。
    防止每次重建按钮反复 add/remove 出现的 race（阳哥 2026-08-31 「点高配不切」）。"""
    src = _read()
    assert '$("pageTabs").addEventListener("click"' in src, (
        "#pageTabs 缺事件委托；renderPageTabs 重建按钮时若逐个 add listener，"
        "会出现'点不切'的竞态；必须挂在容器上、读 e.target.closest('.pagetab').dataset.i"
    )
