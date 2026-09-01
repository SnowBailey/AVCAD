"""R11 图例库校正页守卫（阳哥 2026-09-01）。

语义（本文件守的就是这三条）：

  · 页面列表 = **图例库已有** ∪ **主库里尚未被图例覆盖的可出图产品**
  · 每条保存落**图例库**（走 /api/legend），主库在此**只读展示**，
    落完由 R10 自动把物理端口数反推回主库
  · 主库里没有对应设备模板的类别（配件 / 线缆 / 后置型号）不该进列表 ——
    否则「待确认」列表会被几百条建不了图例的噪音淹掉

★ 两库路径都必须重定向：图例库是**永久文档**，写坏了要人救；
  主库写坏了测试全绿但 git diff 冒出莫名其妙的参数（R10 踩过）。
"""
from __future__ import annotations

import json

import pytest

import avcad.ui.app as app
import avcad.workflow.legend_store as lstore
import avcad.workflow.legend_sync as lsync
from avcad.workflow.legend_builder import infer_from_product

# 真实主库路径：只**读**取样例产品，绝不写
from avcad.data.catalog_resolver import DEFAULT_JSON as REAL_CATALOG  # noqa: E402

# 主库里人工校正过 ports_override 的会议主机（4×CH + 4×PHX + 1×MIX + 1×BOX）
CF6300 = ("IPS", "CF6300", "MIC_HOST")


def _real_product(brand: str, model: str, category: str):
    from pathlib import Path
    data = json.loads(Path(REAL_CATALOG).read_text(encoding="utf-8"))
    for p in data["products"]:
        if not isinstance(p, dict):
            continue
        if (str(p.get("brand") or "").upper() == brand
                and str(p.get("model") or "").upper() == model
                and str(p.get("category") or "") == category):
            return p
    return None


def _call(path, body):
    return app._dispatch(path, json.dumps(body or {}))


def _write_catalog(tmp_path, products) -> str:
    cat = tmp_path / "eko_catalog.json"
    cat.write_text(json.dumps({"products": products}, ensure_ascii=False),
                   encoding="utf-8")
    return str(cat)


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """把两个落盘路径 + 页面读主库的路径都指向 tmp。"""
    def _setup(products, legend=None):
        cat = _write_catalog(tmp_path, products)
        monkeypatch.setattr(lsync, "DEFAULT_CATALOG", tmp_path / "eko_catalog.json")
        monkeypatch.setattr(lstore, "DEFAULT_CACHE",
                            tmp_path / "legend_library.json")
        monkeypatch.setattr(app, "_CATALOG_PATH", cat)
        monkeypatch.setattr(app, "_CATALOG", {"data": None, "mtime": 0})
        if legend is not None:
            (tmp_path / "legend_library.json").write_text(
                json.dumps({"schema": "avcad.legend-library/1",
                            "legends": legend}, ensure_ascii=False),
                encoding="utf-8")
        return cat
    return _setup


# ============================================================
# infer_from_product：主库产品 -> 图例初值
# ============================================================

def test_infer_reads_manual_ports_override():
    """主库 MANUAL_PARAMS 里人工校正过的端口必须成为图例初值。

    前端 defaultPorts 只按类别给「MIXER 8进4出」这类猜测值，
    CF6300 的 4×CH + 4×PHX + 1×MIX + 1×BOX 只能靠主库 ports_override 还原。
    """
    prod = _real_product(*CF6300)
    assert prod is not None, "主库里应有 IPS CF6300 / MIC_HOST"
    lg = infer_from_product(prod)
    assert lg is not None
    labels = sorted(p.label for p in lg.ports)
    assert labels == ["BOX", "CH", "MIX", "PHX"], labels


def test_infer_returns_none_for_undrawable_category():
    """类别为空 / 无对应设备模板 -> None（调用方降级为「手工添加」）。"""
    assert infer_from_product({"brand": "X", "model": "Y", "category": ""}) is None
    assert infer_from_product({"brand": "X", "model": "Y",
                               "category": "NOT_A_CATEGORY"}) is None
    assert infer_from_product({}) is None
    assert infer_from_product(None) is None


def test_infer_tolerates_dirty_params():
    """主库 params 里有历史脏数据时不能 500，返回 None 让人工填。"""
    lg = infer_from_product({"brand": "IPS", "model": "DIRTY",
                             "category": "PROCESSOR",
                             "params": {"ports_override": "???不是JSON???",
                                        "inputs": "abc"}})
    # 要么推断出模板端口，要么返回 None；绝不能抛异常
    assert lg is None or isinstance(lg.ports, list)


# ============================================================
# /api/legend-catalog：列表合并
# ============================================================

def test_list_merges_legend_and_catalog(isolate):
    """图例库已有 1 条 + 主库未覆盖 1 条 -> 两条都在，图例项在前。"""
    isolate(
        products=[{"brand": "IPS", "model": "UNCONFIRMED-1",
                   "category": "PROCESSOR", "params": {"inputs": 4, "outputs": 2},
                   "features": [], "name": "处理器"}],
        legend=[{"brand": "IPS", "model": "CONFIRMED-1", "category": "PROCESSOR",
                 "revision": 3, "ports": [
                     {"signal": "XLR", "role": "in", "side": "left",
                      "count": 8, "label": "IN", "air": False}],
                 "slots": [], "note": ""}],
    )
    r = _call("/api/legend-catalog", {"action": "list"})
    items = r["items"]
    assert len(items) == 2, [i["model"] for i in items]
    assert items[0]["source"] == "legend" and items[0]["revision"] == 3
    assert items[1]["source"] == "catalog" and items[1]["revision"] == 0
    assert items[1]["inferred"] is True
    assert items[1]["ports"], "未覆盖项应带引擎推断出的端口初值"


def test_list_skips_already_confirmed(isolate):
    """图例库已覆盖的型号不再作为「未确认」重复出现。"""
    isolate(
        products=[{"brand": "IPS", "model": "SAME", "category": "PROCESSOR",
                   "params": {}, "features": []}],
        legend=[{"brand": "IPS", "model": "SAME", "category": "PROCESSOR",
                 "revision": 1, "ports": [], "slots": [], "note": ""}],
    )
    items = _call("/api/legend-catalog", {"action": "list"})["items"]
    assert len(items) == 1
    assert items[0]["source"] == "legend"


def test_list_skips_undrawable_products(isolate):
    """不可出图的产品（类别空 / 无模板）不进列表，避免淹没待确认。"""
    isolate(products=[
        {"brand": "IPS", "model": "OK-1", "category": "PROCESSOR",
         "params": {}, "features": []},
        {"brand": "IPS", "model": "NOCAT", "category": "", "params": {}},
        {"brand": "IPS", "model": "CABLE", "category": "CABLE", "params": {}},
    ])
    models = [i["model"] for i in
              _call("/api/legend-catalog", {"action": "list"})["items"]]
    assert models == ["OK-1"], models


def test_list_only_missing(isolate):
    """only_missing 只看图例库尚未覆盖的。"""
    isolate(
        products=[{"brand": "IPS", "model": "UNCONFIRMED-1",
                   "category": "PROCESSOR", "params": {}, "features": []}],
        legend=[{"brand": "IPS", "model": "CONFIRMED-1", "category": "PROCESSOR",
                 "revision": 1, "ports": [], "slots": [], "note": ""}],
    )
    items = _call("/api/legend-catalog",
                  {"action": "list", "only_missing": True})["items"]
    assert [i["model"] for i in items] == ["UNCONFIRMED-1"]


def test_meta_reports_brand_confirmed_counts(isolate):
    """meta 给出每个品牌的「主库条数 / 已确认条数」。"""
    isolate(
        products=[{"brand": "IPS", "model": "A", "category": "PROCESSOR",
                   "params": {}, "features": []},
                  {"brand": "IPS", "model": "B", "category": "PROCESSOR",
                   "params": {}, "features": []}],
        legend=[{"brand": "IPS", "model": "A", "category": "PROCESSOR",
                 "revision": 1, "ports": [], "slots": [], "note": ""}],
    )
    m = _call("/api/legend-catalog", {"action": "meta"})
    ips = next(b for b in m["brands"] if b["brand"] == "IPS")
    assert ips["count"] == 2 and ips["confirmed"] == 1
    assert m["legend_count"] == 1


# ============================================================
# 端到端：在这页保存 -> 落图例库 -> R10 反推主库
# ============================================================

def test_save_from_page_writes_legend_then_reverses_catalog(isolate, tmp_path):
    """改端口 -> 图例库 +1 条，主库 params 拿到 legend_rev + 聚合端口数。"""
    cat = isolate(products=[
        {"brand": "IPS", "model": "PAGE-SAVE", "category": "PROCESSOR",
         "params": {"inputs": 1, "outputs": 1, "dsp": "yes"}, "features": []},
    ])
    res = _call("/api/legend", {
        "brand": "IPS", "model": "PAGE-SAVE", "category": "PROCESSOR",
        "ports": [{"signal": "XLR", "role": "in", "side": "left",
                   "count": 12, "label": "IN", "air": False},
                  {"signal": "XLR", "role": "out", "side": "right",
                   "count": 8, "label": "OUT", "air": False},
                  {"signal": "RF", "role": "in", "side": "top",
                   "count": 2, "label": "ANT", "air": True}],
        "slots": [], "note": "",
    })
    assert res["ok"] and res["catalog_synced"] is True
    # ① 图例库落盘
    lib = json.loads((tmp_path / "legend_library.json").read_text(encoding="utf-8"))
    assert len(lib["legends"]) == 1
    assert lib["legends"][0]["revision"] == 1
    # ② 主库被反推（air 口不计入物理端口）
    prod = json.loads(_read(cat))["products"][0]
    assert prod["params"]["inputs"] == 12
    assert prod["params"]["outputs"] == 8
    assert prod["params"]["legend_rev"] == 1
    assert prod["params"]["dsp"] == "yes", "非端口字段必须保留"
    # ③ 保存后再查列表，它应变成「已确认」
    items = _call("/api/legend-catalog", {"action": "list"})["items"]
    assert [i["source"] for i in items] == ["legend"]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ============================================================
# find_product_index：类别漂移回退（与 LegendStore.get 同规则）
# ============================================================

def test_find_product_index_falls_back_when_single_match():
    """图例类别与主库不同、但该型号在主库唯一 -> 认这一条（否则反推静默失效）。"""
    from avcad.workflow.legend_sync import find_product_index
    products = [{"brand": "IPS", "model": "X", "category": "PROCESSOR"}]
    assert find_product_index(products, "IPS", "X", "MIXER") == 0


def test_find_product_index_requires_exact_when_multi_match():
    """同型号多条（跨类别）-> 必须类别精确，防误覆盖别的类别。"""
    from avcad.workflow.legend_sync import find_product_index
    products = [{"brand": "IPS", "model": "X", "category": "PROCESSOR"},
                {"brand": "IPS", "model": "X", "category": "MIC_HOST"}]
    assert find_product_index(products, "IPS", "X", "MIC_HOST") == 1
    assert find_product_index(products, "IPS", "X", "MIXER") == -1
    # 空类别仍然不匹配（既有契约：防误覆盖）
    assert find_product_index(products, "IPS", "X", "") == -1
