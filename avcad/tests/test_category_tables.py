"""设备类别「展示映射表」的一致性守卫。

背景（2026-08-31）：`MIC_HOST`（会议主机）这个类别在规格库、主库、链路、
连线里都是齐全的，唯独漏在了渲染层两张展示映射表里——

  - `render/draw.CATEGORY_CODE` 缺它 → 匿名模式 fallback 成
    `category[:3].upper()` = **"MIC"**，和调音台的 **"MIX"** 只差一个字母，
    图上根本分不清；
  - `render/draw.CATEGORY_CN` 缺它 → 中文名退化成原始英文 "MIC_HOST"。

同一个类别在 4 处各维护一份名单，靠人肉同步必然再漏。这个文件把「规格库里
有的类别，所有展示映射表都必须有」变成一条自动断言——**以后新增类别，忘了
同步任何一处都会立刻测试失败**，不用再等用户看到一张画错的图。

（另一个同类问题是 `validate/checks.py` 的 KNOWN 白名单漏了 MIC_HOST，导致
每台会议主机都报「未知设备类型」ERROR。那个已改成从 load_specs() 动态取，
从根上消除了这份名单。）
"""
from __future__ import annotations

import pytest

import os

from avcad.model.specs import load_specs, DATA_DIR
from avcad.render.draw import CATEGORY_CN, CATEGORY_CODE
from avcad.topology.chain import STAGE_LABELS
from avcad.data.catalog_resolver import CAT_SPEC


def _spec_categories():
    return sorted(load_specs())


# STAGE_LABELS 是**链路阶段名**而非类别名：处理器在链路里被拆成「前置/后置」
# 两个虚拟阶段，所以表里没有 PROCESSOR 本身，却多出这两个虚拟阶段。
# ★ 这些 stage 不是规格库里的设备类别，是 assign_stages 给特定类别打的标记：
#   PROC_PRE / PROC_POST = 处理器前置/后置（同一类处理器可分属两种 stage）
#   SIDE = 不进入主链路的「侧层设备」（如 IO 与调音台平级，仅通过 DANTE 经交换机互通）
_VIRTUAL_STAGES = {"PROC_PRE", "PROC_POST", "SIDE"}
# 规格库里有这些类别，但它们已被替代 stage 承载，链路上不再以自身出现：
#   PROCESSOR -> PROC_PRE / PROC_POST（前置/后置两种）
#   IO        -> SIDE（侧层设备，不进入主链路）
_REPLACED_BY_STAGES = {"PROCESSOR", "IO"}


def test_every_category_has_chinese_name():
    """每个类别都要有中文名，否则图上会显示原始英文枚举。"""
    missing = [c for c in _spec_categories() if not CATEGORY_CN.get(c)]
    assert not missing, (
        f"以下类别缺少 CATEGORY_CN 中文名（render/draw.py）：{missing}。"
        f"新增类别时请同步该表。")


def test_every_category_has_anon_code():
    """每个类别都要有匿名代号，否则会 fallback 成 category[:3].upper()。"""
    missing = [c for c in _spec_categories() if not CATEGORY_CODE.get(c)]
    assert not missing, (
        f"以下类别缺少 CATEGORY_CODE 匿名代号（render/draw.py）：{missing}。"
        f"fallback 是 category[:3].upper()，容易与别的类别撞成近似串"
        f"（历史上 MIC_HOST 就撞成了 MIC / MIX）。")


def test_every_category_has_stage_label():
    """链路阶段名也要齐全（报告与图例说明用）。

    PROCESSOR 例外：它在链路里由 PROC_PRE / PROC_POST 两个虚拟阶段承载。
    """
    need = set(_spec_categories()) - _REPLACED_BY_STAGES
    missing = sorted(c for c in need if not STAGE_LABELS.get(c))
    assert not missing, (
        f"以下类别缺少 STAGE_LABELS 阶段名（topology/chain.py）：{missing}")


def test_anon_codes_are_unique():
    """匿名代号必须唯一——同号会让两类设备在图上无法区分。"""
    codes = {c: CATEGORY_CODE.get(c) or (c[:3].upper() or "DEV")
             for c in _spec_categories()}
    dupes = {}
    for cat, code in codes.items():
        dupes.setdefault(code, []).append(cat)
    clash = {k: v for k, v in dupes.items() if len(v) > 1}
    assert not clash, f"匿名代号重复（含 fallback 后的撞车）：{clash}"


def test_anon_codes_are_not_confusable():
    """代号之间不许只差一个字符——MIC / MIX 这类在图纸上无法分辨。

    只查「长度相同且仅一位不同」的强混淆对，避免误报（如 IO / IO2 本就不同）。
    """
    codes = {c: CATEGORY_CODE.get(c) or (c[:3].upper() or "DEV")
             for c in _spec_categories()}
    bad = []
    items = list(codes.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i][1], items[j][1]
            if len(a) == len(b) and len(a) > 1:
                diff = sum(1 for x, y in zip(a, b) if x != y)
                if diff == 1:
                    bad.append((items[i][0], a, items[j][0], b))
    assert not bad, (
        f"以下类别的匿名代号只差一个字符，图纸上无法分辨：{bad}。"
        f"请改 render/draw.CATEGORY_CODE。")


def test_every_category_has_spec_template():
    """每个类别都要有「类别 -> 规格 yaml」的映射，且该 yaml 真实存在。

    缺映射时主库识别出的类别拿不到规格模板，端口会整片消失。
    """
    missing = [c for c in _spec_categories() if not CAT_SPEC.get(c)]
    assert not missing, (
        f"以下类别缺少 CAT_SPEC 映射（data/catalog_resolver.py）：{missing}")
    nofile = [(c, f) for c, f in CAT_SPEC.items()
              if not os.path.exists(os.path.join(DATA_DIR, f + ".yaml"))]
    assert not nofile, (
        f"CAT_SPEC 指向了不存在的规格 yaml：{nofile}（DATA_DIR={DATA_DIR}）")


@pytest.mark.parametrize("table,name", [
    (CATEGORY_CN, "CATEGORY_CN"),
    (CATEGORY_CODE, "CATEGORY_CODE"),
    (STAGE_LABELS, "STAGE_LABELS"),
])
def test_tables_have_no_stale_entries(table, name):
    """映射表里不该残留规格库已删除的类别（避免表名单无限膨胀）。"""
    known = set(_spec_categories())
    if name == "STAGE_LABELS":
        known |= _VIRTUAL_STAGES
    stale = [c for c in table if c not in known and c.isupper()]
    assert not stale, (
        f"{name} 里有规格库不存在的类别：{stale}。"
        f"若确属删除，请一并清理映射表。")
