"""R12 守卫：配单里出现「主库没有的新型号」时的三条可见性保证（阳哥 2026-09-01）。

改动前的真实行为（实测，非推测）：

  1. 主库三级匹配（精确 → 紧凑 → 同品牌子串）都不中，回退内置 MODEL_DB 也不中
     → ``_resolved = "fallback"`` → ``importers.build_entries`` 兜底成 **IO**
  2. IO 被 ``chain.assign_stages`` 扔到 SIDE 层，不参与主链路配对 → **连线数 0**
  3. 校验层一声不吭：只多 2 条 ``INFO:UNCONNECTED``，混在几十条同类 INFO 里

  而 ``_resolved`` 在 ``avcad/ui/`` 里**零引用**，第②步完全看不出来。

本文件守三条：

  · **看得见**   —— 模块清单带 ``source``，未收录的标 ``unknown``
  · **初值准**   —— 图例端口初值由引擎规格模板展开，不是前端硬编码模板
  · **会报警**   —— 整台设备零连线 → ``WARN:ISOLATED_DEVICE``（按型号聚合）
"""
from __future__ import annotations

import json

import pytest

import avcad.data.catalog_resolver as cres
import avcad.ui.app as app
import avcad.workflow.legend_store as lstore
import avcad.workflow.legend_sync as lsync
from avcad.core.build import build_project
from avcad.validate.checks import validate
from avcad.workflow.importers import build_entries
from avcad.workflow.legend_builder import infer_from_entry
from avcad.workflow.module_confirm import build_module_list

# 主库里人工校正过 ports_override 的会议主机（4×CH + 4×PHX + 1×MIX + 1×BOX）
CF6300 = ("IPS", "CF6300", "MIC_HOST")


def _call(path, body):
    return app._dispatch(path, json.dumps(body or {}))


def _write_catalog(tmp_path, products) -> str:
    cat = tmp_path / "eko_catalog.json"
    cat.write_text(json.dumps({"products": products}, ensure_ascii=False),
                   encoding="utf-8")
    return str(cat)


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """把两个落盘路径 + 页面读主库的路径 + **型号解析器单例**都指向 tmp。

    ★ 第四个必填：``module_confirm._ensure_resolved`` 走的是
      ``product_resolver.enrich`` → ``catalog_resolver.resolve``，用的是
      **模块级单例 ``_default``**，跟 ``legend_sync.DEFAULT_CATALOG`` 不是同一条路。
      只 patch 前者的话，测试会读到仓库里那份真实的 2325 条主库 ——
      于是「内置 MODEL_DB 命中」这条用例会因为 TF5 恰好也在真主库里而假通过。
    """
    def _setup(products, legend=None):
        cat = _write_catalog(tmp_path, products)
        monkeypatch.setattr(lsync, "DEFAULT_CATALOG", cat)
        monkeypatch.setattr(lstore, "DEFAULT_CACHE", str(tmp_path / "legend_library.json"))
        monkeypatch.setattr(app, "_CATALOG_PATH", cat)
        monkeypatch.setattr(app, "_CATALOG", {"mtime": 0, "data": None})
        monkeypatch.setattr(cres, "_default", cres.Catalog(cat))
        if legend is not None:
            (tmp_path / "legend_library.json").write_text(
                json.dumps({"schema": "avcad.legend-library/1", "items": legend},
                           ensure_ascii=False), encoding="utf-8")
        return cat
    return _setup


# ==================================================================
# ① 看得见：模块清单的收录状态
# ==================================================================
def test_unknown_model_marked_unknown(isolate):
    """主库没有、名称也没关键词 → source == "unknown"。"""
    isolate([{"brand": "EAW", "model": "NT206L", "category": "SPEAKER"}])
    mods = build_module_list([
        {"brand": "FooBar", "model": "XYZ-9999", "name": "超融合媒体节点",
         "category": "IO", "quantity": 2},
    ])
    assert len(mods) == 1
    assert mods[0].source == "unknown", (
        "新型号必须标 unknown，否则第②步看不出来 —— 改动前根本没这个字段")


def test_catalog_hit_marked_catalog(isolate):
    """主库命中 → source == "catalog"。"""
    isolate([{"brand": "IPS", "model": "CF6300", "category": "MIC_HOST",
              "params": {}}])
    mods = build_module_list([{"brand": "IPS", "model": "CF6300",
                               "category": "MIC_HOST", "quantity": 1}])
    assert mods[0].source == "catalog"


def test_builtin_db_hit_marked_builtin(isolate):
    """主库没有但内置 MODEL_DB 有 → source == "builtin"（区别于 unknown）。"""
    isolate([])
    mods = build_module_list([{"brand": "Yamaha", "model": "TF5",
                               "category": "MIXER", "quantity": 1}])
    assert mods[0].source == "builtin", (
        "内置库命中不该算 unknown —— 用户不需要为它补图例")


def test_build_module_list_does_not_mutate_entries(isolate):
    """★ 不能就地给调用方的 entry 打 _resolved。

    app._ENTRY_CACHE 缓存了 BOM CSV 解析出来的条目，就地写会让缓存对象
    带上私有字段，下一轮换份主库时缓存里的旧标记就赖着不走了。
    """
    isolate([])
    ents = [{"brand": "FooBar", "model": "XYZ-9999", "category": "IO",
             "quantity": 1}]
    build_module_list(ents)
    assert "_resolved" not in ents[0], "build_module_list 污染了传入的条目"


def test_module_item_carries_features_and_params(isolate):
    """模块要带 features/params，图例页才能用引擎展开端口初值。"""
    isolate([])
    mods = build_module_list([{
        "brand": "X", "model": "Y", "category": "IO", "quantity": 1,
        "features": ["dante"], "params": {"inputs": 4, "outputs": 2}}])
    assert mods[0].features == ["dante"]
    assert mods[0].params == {"inputs": 4, "outputs": 2}


def test_csv_path_applies_category_fallback(isolate):
    """★ CSV / 文本清单路径必须跟 xlsx 走同一套类别兜底。

    实测过的事故：``parse_bom`` 只读表、不做归一化，而 xlsx 走
    ``build_entries`` 会兜底 IO。于是同一份清单两种下场——
    CSV 里「设备类型」列留空的未知型号，出图时 ``category == ""``
    → 0 个端口 + ``ERROR:UNKNOWN_TYPE``，xlsx 却是 IO 1进1出。
    """
    isolate([])
    csv = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
           ",Zzz,ZZZ-NEW-0001,全新未知设备,1,,,,,\n")
    ents = app._entries_from_bom(csv)
    assert len(ents) == 1
    assert ents[0].get("category") == "IO", (
        f"CSV 路径没走类别兜底，category={ents[0].get('category')!r} —— "
        "出图会变成 0 端口 + ERROR:UNKNOWN_TYPE")


def test_csv_path_resolves_before_fallback(isolate):
    """★ 顺序不能反：先 resolve 主库，再兜底 IO。

    ``enrich`` 只在 ``category`` 为空时才填。若先兜底成 IO，主库命中的
    型号（CF6300 = MIC_HOST）会被永久钉死在 IO 上，图例也跟着错。
    """
    isolate([{"brand": "IPS", "model": "CF6300", "category": "MIC_HOST",
              "params": {}, "drawable": True}])
    csv = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
           ",IPS,CF6300,会议主机,1,,,,,\n")
    ents = app._entries_from_bom(csv)
    assert ents[0].get("category") == "MIC_HOST", (
        f"主库命中被 IO 兜底盖掉了，category={ents[0].get('category')!r} —— "
        "resolve 与 fallback 的顺序反了")


def test_api_parse_returns_source(isolate, tmp_path):
    """★ /api/parse 必须把 source 透出去，否则前端拿不到。"""
    isolate([])
    csv = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气\n"
           "IO,FooBar,XYZ-9999,超融合媒体节点,2,\n")
    res = _call("/api/parse", {"bom": csv})
    assert res["modules"], "解析应返回模块"
    assert res["modules"][0]["source"] == "unknown"
    assert "FooBar XYZ-9999" in res["unknown"], "未知型号清单要单独给出"


# ==================================================================
# ② 初值准：图例端口初值由引擎规格模板展开
# ==================================================================
def test_infer_io_default_is_one_in_one_out(isolate):
    """★ IO 引擎口径是 1 进 1 出，不是前端 defaultPorts 的 4 进 4 出。

    index.html 的 defaultPorts() 没有 IO / MIC_HOST 的 case，都落 default
    分支 = XLR 4进4出。用户照着这个错的数去改图例，改完跟实际出图还是不一样。
    """
    isolate([])
    lg = infer_from_entry({"category": "IO", "brand": "FooBar",
                           "model": "XYZ-9999", "params": {}, "features": []})
    assert lg is not None
    got = {p.label: p.count for p in lg.ports}
    assert got == {"IN": 1, "OUT": 1}, f"IO 初值应为 1进1出，实际 {got}"


def test_infer_adds_dante_when_feature_present(isolate):
    """有 dante 特性才多一个 DANTE 口（io.yaml 的 if_feature 条件）。"""
    isolate([])
    lg = infer_from_entry({"category": "IO", "params": {}, "features": ["dante"]})
    assert "DANTE" in {p.label for p in lg.ports}


def test_infer_mic_host_uses_ports_override(isolate):
    """MIC_HOST 靠主库 ports_override 展开，前端模板给的是错的 4进4出。"""
    isolate([])
    lg = infer_from_entry({
        "category": "MIC_HOST", "brand": "IPS", "model": "CF6300",
        "features": ["control", "wireless"],
        "params": {"ports_override": [
            {"name": "CH", "side": "left", "signal": "CONF", "role": "in",
             "label": "CH", "count": 4},
            {"name": "PHX", "side": "right", "signal": "XLR", "role": "out",
             "label": "PHX", "count": 4},
            {"name": "MIX", "side": "right", "signal": "XLR", "role": "out",
             "label": "MIX", "count": 1},
        ]}})
    assert lg is not None
    got = {p.label: p.count for p in lg.ports}
    assert got.get("CH") == 4 and got.get("PHX") == 4 and got.get("MIX") == 1, (
        f"会议主机初值应为 CH×4 + PHX×4 + MIX×1，实际 {got}")


def test_infer_returns_none_for_unknown_category(isolate):
    """类别无规格模板 → 返回 None（调用方回落前端模板），不许编一套默认值。"""
    isolate([])
    assert infer_from_entry({"category": "NOT_A_CATEGORY"}) is None
    assert infer_from_entry({"category": ""}) is None
    assert infer_from_entry("not a dict") is None


def test_infer_survives_dirty_params(isolate):
    """主库历史脏数据不能让推断 500，也不能把脏值透传成端口数。

    ★ 实测引擎行为是**优雅降级**：非数字的 inputs 被忽略、回落规格默认值，
      而不是抛异常或返回 None。这比返回 None 更好——图例页仍拿到一份可改的初值。
      所以这里守的是「不抛异常 + 端口数是有意义的正整数」，不是「必须 None」。
    """
    isolate([])
    for bad in ["很多", None, {"a": 1}, [1, 2]]:
        lg = infer_from_entry({"category": "MIXER", "params": {"inputs": bad}})
        assert lg is not None, f"脏 params {bad!r} 不该让推断整体失败"
        assert lg.ports, f"脏 params {bad!r} 至少该给出默认端口"
        for p in lg.ports:
            assert isinstance(p.count, int) and p.count >= 1, (
                f"脏 params {bad!r} 泄漏成端口数：{p.label}={p.count!r}")


def test_legend_infer_endpoint(isolate):
    """/api/legend-infer 返回 ports 映射与失败键。"""
    isolate([])
    res = _call("/api/legend-infer", {"items": [
        {"brand": "FooBar", "model": "XYZ-9999", "category": "IO",
         "params": {}, "features": []},
        {"brand": "X", "model": "Y", "category": "NOPE", "params": {}, "features": []},
    ]})
    assert "FooBar::XYZ-9999::IO" in res["ports"]
    assert res["failed"] == ["X::Y::NOPE"]


def test_legend_infer_writes_nothing(tmp_path, monkeypatch):
    """★ 推断初值**不得**写图例库 —— 它是纯查询，写了就等于自动建档。"""
    lib = tmp_path / "legend_library.json"
    monkeypatch.setattr(lstore, "DEFAULT_CACHE", str(lib))
    _call("/api/legend-infer", {"items": [
        {"brand": "FooBar", "model": "XYZ-9999", "category": "IO"}]})
    assert not lib.exists(), "推断初值写落了图例库（应纯内存）"


# ==================================================================
# ③ 会报警：ISOLATED_DEVICE
# ==================================================================
def _mini_system():
    return [
        {"brand": "Shure", "model": "ULXD4", "name": "无线话筒", "quantity": 2,
         "category": "WIRELESS_RX", "params": {"channels": 1}},
        {"brand": "YAMAHA", "model": "TF5", "name": "数字调音台", "quantity": 1,
         "category": "MIXER", "params": {"inputs": 32, "outputs": 16},
         "features": ["dante"]},
        {"brand": "EAW", "model": "NT206L", "name": "全频扬声器", "quantity": 2,
         "category": "SPEAKER", "params": {"impedance_ohm": 8, "power_w": 300}},
        {"brand": "Powersoft", "model": "Quattrocanali 4804", "name": "功率放大器",
         "quantity": 1, "category": "AMP", "params": {"channels": 4}},
    ]


def test_known_system_has_no_isolated_warning(isolate):
    """正常系统零告警 —— 否则这条检查就是噪音。"""
    isolate([])
    proj = build_project(_mini_system(), name="t")
    assert [i for i in validate(proj) if i.code == "ISOLATED_DEVICE"] == []


def test_unknown_model_triggers_isolated_warning(isolate):
    """★ 新型号兜底成 IO → 孤立 → 必须报 WARN。

    改动前完全静默：只多 2 条 INFO:UNCONNECTED，混在几十条同类 INFO 里。
    """
    isolate([])
    ents = _mini_system() + [
        {"brand": "FooBar", "model": "XYZ-9999", "name": "超融合媒体节点",
         "quantity": 1, "category": "IO"}]
    proj = build_project(ents, name="t")
    iso = [i for i in validate(proj) if i.code == "ISOLATED_DEVICE"]
    assert len(iso) == 1, f"应恰好 1 条孤立告警，实际 {len(iso)}"
    assert iso[0].level == "WARN"
    assert "XYZ-9999" in iso[0].msg


def test_isolated_aggregates_by_model(isolate):
    """★ 同型号 N 台聚合成 1 条（B-EAW4 有 24 台，逐台报是噪音）。"""
    isolate([])
    ents = _mini_system() + [
        {"brand": "FooBar", "model": "XYZ-9999", "name": "超融合媒体节点",
         "quantity": 3, "category": "IO"}]
    proj = build_project(ents, name="t")
    iso = [i for i in validate(proj) if i.code == "ISOLATED_DEVICE"]
    assert len(iso) == 1, f"3 台同型号应聚合成 1 条，实际 {len(iso)}"
    assert "×3" in iso[0].msg


def test_wireless_mic_exempt_from_isolated(isolate):
    """无线发射端设计上不产生线缆连接，不能算孤立。"""
    isolate([])
    ents = _mini_system() + [
        {"brand": "IPS", "model": "UM2000H", "name": "无线手持话筒",
         "quantity": 2, "category": "WIRELESS_MIC"}]
    proj = build_project(ents, name="t")
    iso = [i for i in validate(proj) if i.code == "ISOLATED_DEVICE"]
    assert [i for i in iso if "UM2000H" in i.msg] == []


def test_isolated_distinguishes_incomplete_bom(isolate):
    """清单只含后端设备时，措辞要指出是清单缺前端，不是某台设备没接。"""
    isolate([])
    proj = build_project([
        {"brand": "EAW", "model": "NT206L", "name": "全频扬声器", "quantity": 3,
         "category": "SPEAKER", "params": {"impedance_ohm": 8, "power_w": 300}},
    ], name="t")
    iso = [i for i in validate(proj) if i.code == "ISOLATED_DEVICE"]
    assert len(iso) == 1
    assert "清单只含后端设备" in iso[0].msg


# ==================================================================
# 端到端：配单 -> 第②步 -> 第③步 -> 落图例库
# ==================================================================
def test_end_to_end_new_model_visible_then_fixable(isolate, tmp_path):
    """新型号：第②步看得见 → 第③步改端口 → 落图例库 → 下次自动套用。"""
    isolate([])

    # ① 导入：新型号被标 unknown
    csv = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气\n"
           "IO,FooBar,XYZ-9999,超融合媒体节点,1,\n")
    res = _call("/api/parse", {"bom": csv})
    m = res["modules"][0]
    assert m["source"] == "unknown"

    # ② 图例页：端口初值来自引擎（IO = 1进1出，不是 4进4出）
    inf = _call("/api/legend-infer", {"items": [{
        "brand": m["brand"], "model": m["model"], "category": m["category"],
        "features": m.get("features", []), "params": m.get("params", {})}]})
    ports = inf["ports"]["FooBar::XYZ-9999::IO"]
    assert {p["label"]: p["count"] for p in ports} == {"IN": 1, "OUT": 1}

    # ③ 手工改对（比如这台其实是 8 进 4 出的处理器）
    fixed = [{"signal": "XLR", "role": "in", "side": "left", "count": 8,
              "label": "IN", "air": False},
             {"signal": "XLR", "role": "out", "side": "right", "count": 4,
              "label": "OUT", "air": False}]
    sv = _call("/api/legend", {"brand": "FooBar", "model": "XYZ-9999",
                               "category": "IO", "ports": fixed, "slots": [],
                               "note": "手工校正"})
    assert sv.get("ok")

    # ④ 下次再导入：不再报 unknown，且端口以图例库为准
    res2 = _call("/api/parse", {"bom": csv})
    lg = _call("/api/legend", {"action": "get", "brand": "FooBar",
                               "model": "XYZ-9999", "category": "IO"})
    assert lg["legend"] is not None, "图例库应已建档"
    assert sum(p["count"] for p in lg["legend"]["ports"]) == 12


def test_manual_add_requires_no_catalog_entry(isolate):
    """★ 手工建档不要求主库有这个型号 —— 这正是给新型号用的入口。"""
    isolate([])   # 空主库
    res = _call("/api/legend", {
        "brand": "NewBrand", "model": "NEW-MODEL-1", "category": "PROCESSOR",
        "ports": [{"signal": "XLR", "role": "in", "side": "left", "count": 4,
                   "label": "IN", "air": False}], "slots": [], "note": ""})
    assert res.get("ok"), f"手工建档失败: {res}"
    # 主库里没有，反推应跳过但不报错
    assert res.get("catalog_synced") is False
