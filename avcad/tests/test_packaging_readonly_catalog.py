"""R13 守卫：打包版主库是只读内置基线，保存图例不能崩（2026-09-01）。

★ 起因：出 dmg / exe 前排查「打包后哪些写盘路径会打到只读包内」，发现
  ``/api/legend`` PUT 保存图例的必经之路上有 ``apply_reverse_to_catalog()``，
  它往 ``avcad/data/eko_catalog.json`` 写盘 —— 打包后这个文件在**只读**的
  .app / Program Files 里，一写就是 PermissionError。

  两个让这个坑特别阴的细节：

  1. 崩在 ``st.save()`` **之后** → 图例其实已经存了，但接口返回 500，
     前端表现为「保存失败」，用户会反复重试、重复建档。
  2. **只有主库里已有的型号才触发**（新型号 find_product_index 返回 -1，
     直接 return，反而侥幸不写）→ 开发时拿新型号测，永远复现不了。

本文件守四条：

  · 只读   -> **跳过**反推，返回 skipped 非空，绝不抛异常
  · 可写   -> 照常写回主库（别把 R10 的功能关死）
  · 端到端 -> 只读主库下 /api/legend PUT 仍然 ok，且图例真的落盘
  · 主动改主库 -> 明确抛 PermissionError，而不是淹没的 OSError
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import avcad.data.catalog_resolver as cres
import avcad.ui.app as app
import avcad.workflow.legend_store as lstore
import avcad.workflow.legend_sync as lsync

# 主库里真实存在的调音台（反推要能命中）
TF5 = {"brand": "Yamaha", "model": "TF5", "category": "MIXER",
       "params": {"inputs": 8, "outputs": 4}, "features": []}

LEGEND_PORTS = [
    {"signal": "XLR", "role": "in", "side": "left", "count": 12,
     "label": "IN", "air": False},
    {"signal": "XLR", "role": "out", "side": "right", "count": 2,
     "label": "OUT", "air": False},
]


def _call(path, body):
    return app._dispatch(path, json.dumps(body or {}))


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """三处落盘路径 + 型号解析器单例全部指向 tmp（与 R12 守卫同一套）。"""
    def _setup(products):
        cat = tmp_path / "eko_catalog.json"
        cat.write_text(json.dumps({"products": products}, ensure_ascii=False),
                       encoding="utf-8")
        monkeypatch.setattr(lsync, "DEFAULT_CATALOG", cat)
        monkeypatch.setattr(lstore, "DEFAULT_CACHE",
                            tmp_path / "legend_library.json")
        monkeypatch.setattr(app, "_CATALOG_PATH", str(cat))
        monkeypatch.setattr(app, "_CATALOG", {"data": None, "mtime": 0})
        monkeypatch.setattr(cres, "_default", cres.Catalog(str(cat)))
        return cat
    return _setup


# ============================================================
# ① 只读 -> 跳过，不抛
# ============================================================
def test_reverse_skips_when_catalog_readonly(isolate, monkeypatch):
    """主库不可写时跳过反推：不抛异常、不改动文件、留下 skipped 原因。"""
    cat = isolate([dict(TF5)])
    monkeypatch.setattr(lsync, "catalog_writable", lambda p: False)
    before = Path(cat).read_text(encoding="utf-8")

    rr = lsync.apply_reverse_to_catalog({
        "brand": "Yamaha", "model": "TF5", "category": "MIXER",
        "ports": LEGEND_PORTS,
    })

    assert rr.matched is False
    assert rr.skipped, (
        "只读跳过必须留下原因。不留的话排障时会把「主库只读」误判成"
        "「主库里没这个型号」，越查越偏。")
    assert Path(cat).read_text(encoding="utf-8") == before, "只读时不该改动主库"
    assert not list(Path(cat).parent.glob("*.bak.*")), "只读时连备份都不该尝试生成"


def test_reverse_writes_when_catalog_writable(isolate):
    """★ 回归保护：可写时必须照常反推，别把 R10 的功能顺手关死。"""
    cat = isolate([dict(TF5)])

    rr = lsync.apply_reverse_to_catalog({
        "brand": "Yamaha", "model": "TF5", "category": "MIXER",
        "ports": LEGEND_PORTS,
    })

    assert rr.matched is True, "TF5 在主库里，应当命中并反推"
    assert rr.skipped == "", f"可写却报告跳过：{rr.skipped}"
    prod = json.loads(Path(cat).read_text(encoding="utf-8"))["products"][0]
    assert prod["params"]["inputs"] == 12, prod["params"]
    assert prod["params"]["outputs"] == 2, prod["params"]


# ============================================================
# ② 端到端：只读主库下保存图例必须成功
# ============================================================
def test_legend_put_survives_readonly_catalog(isolate, monkeypatch):
    """★ 打包版真实场景：装到 /Applications 后保存 TF5 图例，接口必须 ok。"""
    isolate([dict(TF5)])
    monkeypatch.setattr(lsync, "catalog_writable", lambda p: False)

    res = _call("/api/legend", {
        "brand": "Yamaha", "model": "TF5", "category": "MIXER",
        "ports": LEGEND_PORTS, "slots": [], "note": "打包版验证",
    })
    assert res.get("ok"), f"只读主库不该让保存图例失败：{res}"

    got = _call("/api/legend", {"action": "get", "brand": "Yamaha",
                                "model": "TF5", "category": "MIXER"})
    assert got["legend"] is not None, "图例必须真的落盘，不能只是不报错"
    assert sum(p["count"] for p in got["legend"]["ports"]) == 14


def test_legend_put_still_syncs_when_writable(isolate):
    """可写时保存图例要照常反推回主库（R10 行为不变）。"""
    cat = isolate([dict(TF5)])
    _call("/api/legend", {
        "brand": "Yamaha", "model": "TF5", "category": "MIXER",
        "ports": LEGEND_PORTS, "slots": [], "note": "",
    })
    prod = json.loads(Path(cat).read_text(encoding="utf-8"))["products"][0]
    assert prod["params"]["inputs"] == 12, prod["params"]


# ============================================================
# ③ 主动改主库：必须明确报 PermissionError
# ============================================================
def test_save_catalog_raises_permission_error_when_readonly(isolate, monkeypatch):
    """用户**主动**写主库时静默成功是骗人，必须抛明确的 PermissionError。"""
    isolate([dict(TF5)])
    monkeypatch.setattr(app, "catalog_writable", lambda p: False)
    with pytest.raises(PermissionError, match="主库只读"):
        app._save_catalog()


# ============================================================
# ④ catalog_writable 本身：用真实权限位验证
# ============================================================
def test_legend_put_survives_real_readonly_dir(tmp_path, monkeypatch):
    """★ 真实 chmod 555 目录端到端：不 monkeypatch，检测 + 调用方串起来测。

    ★ 为什么还要这条：上面几条用例都 monkeypatch 了 ``catalog_writable``，
      **反向验证实测：把检测函数体改成 ``return True`` 后，只有最后一条红** ——
      也就是说它们测的是「调用方是否正确响应 False」，测不到「检测本身坏了」。
      这条用真实只读目录，把两段逻辑一起串上。
    """
    ro = tmp_path / "ro"
    ro.mkdir()
    cat = ro / "eko_catalog.json"
    cat.write_text(json.dumps({"products": [dict(TF5)]}, ensure_ascii=False),
                   encoding="utf-8")
    os.chmod(ro, 0o555)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("以 root 运行：权限位无效，无法用 chmod 模拟只读")
        monkeypatch.setattr(lsync, "DEFAULT_CATALOG", cat)
        monkeypatch.setattr(lstore, "DEFAULT_CACHE",
                            tmp_path / "legend_library.json")
        monkeypatch.setattr(app, "_CATALOG_PATH", str(cat))
        monkeypatch.setattr(app, "_CATALOG", {"data": None, "mtime": 0})
        monkeypatch.setattr(cres, "_default", cres.Catalog(str(cat)))

        before = cat.read_text(encoding="utf-8")
        res = _call("/api/legend", {
            "brand": "Yamaha", "model": "TF5", "category": "MIXER",
            "ports": LEGEND_PORTS, "slots": [], "note": "真实只读目录",
        })
        assert res.get("ok"), f"真实只读主库下保存图例失败：{res}"
        assert cat.read_text(encoding="utf-8") == before, "只读主库被改动了"
        assert not list(ro.glob("*.bak.*")), "只读目录里不该出现备份文件"
    finally:
        os.chmod(ro, 0o755)


def test_catalog_writable_detects_real_readonly_dir(tmp_path):
    """真实 chmod 验证（CI 非 root 时才有意义）。

    ★ 以 root 运行时权限位无效（os.access 恒 True），无法用 chmod 模拟
      只读 —— 这也是把检测抽成 ``catalog_writable`` 函数的原因：
      上面几条用例靠 monkeypatch 它，不依赖权限位。
    """
    d = tmp_path / "ro"
    d.mkdir()
    (d / "eko_catalog.json").write_text("{}", encoding="utf-8")
    os.chmod(d, 0o555)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("以 root 运行：权限位无效，跳过真实只读目录验证")
        assert lsync.catalog_writable(d / "eko_catalog.json") is False
        assert lsync.catalog_writable(tmp_path / "x.json") is True
    finally:
        os.chmod(d, 0o755)
