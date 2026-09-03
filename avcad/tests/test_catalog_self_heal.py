"""主库 JSON 自修复保险测试（2026-09-03 加）。

验证 catalog_resolver.safe_load_json：
  - 主库损坏 + 同目录有合法 .bak → 透明回退并覆盖回主文件，正常返回数据；
  - 主库损坏 + 无合法 .bak → 仍抛 JSONDecodeError（不静默吞掉）；
  - 只读介质（无写权限）→ 跳过回退直接抛错，不崩；
  - Catalog 加载走同一保险，索引正常构建。

注：本沙箱里 pytest 的 tmp_path 经 broker 拦截会干扰 shutil.copy2，
故这里用真实临时目录（tempfile，落在 /tmp 真实文件系统）验证自愈。
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile

import pytest

from avcad.data.catalog_resolver import Catalog, safe_load_json


def _mkdtemp():
    # 真实文件系统目录，避开沙箱 broker 对 pytest tmp_path 的拦截
    return tempfile.mkdtemp(prefix="avcad-heal-")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _is_invalid(text):
    try:
        json.loads(text)
        return False
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True


def test_safe_load_heals_from_valid_backup():
    d = _mkdtemp()
    good = {"products": [{"brand": "IPS", "model": "UM2000ASD",
                          "category": "ANT_COMBINE", "features": ["wireless"]}]}
    # 真·坏 JSON：defer_reason 缺闭合引号，其后的换行成了字符串内的裸换行
    bad = '{"products": [{"brand": "IPS", "model": "UM2000ASD", "defer_reason": "控制面板(不传音频),\n  "features": ["control"]}]}'
    assert _is_invalid(bad), "测试夹具本身必须是非法 JSON"
    cat = os.path.join(d, "eko_catalog.json")
    bak = os.path.join(d, "eko_catalog.json.bak.20260903010101")
    _write(cat, bad)
    _write(bak, json.dumps(good, ensure_ascii=False))

    data = safe_load_json(cat)

    # 1) 返回的是备份数据
    assert data == good
    # 2) 主文件已被自愈覆盖成合法内容
    assert json.load(open(cat, encoding="utf-8")) == good
    shutil.rmtree(d, ignore_errors=True)


def test_safe_load_raises_when_no_backup():
    d = _mkdtemp()
    bad = '{"products": [{"defer_reason": "坏" 缺引号且缺闭合"}'  # 非法
    assert _is_invalid(bad)
    cat = os.path.join(d, "eko_catalog.json")
    _write(cat, bad)
    with pytest.raises(json.JSONDecodeError):
        safe_load_json(cat)
    shutil.rmtree(d, ignore_errors=True)


def test_catalog_self_heals_and_indexes():
    d = _mkdtemp()
    good = {"products": [{"brand": "Shure", "model": "ULXD4D",
                          "category": "WIRELESS_RX", "features": ["wireless"]}]}
    bad = '{"products": [{"brand": "Shure", "model": "ULXD4D", "category": "WIRELESS_RX", "features": ["wireless"]}]'  # 缺闭合 }
    assert _is_invalid(bad)
    cat = os.path.join(d, "eko_catalog.json")
    bak = os.path.join(d, "eko_catalog.json.bak.20260903020202")
    _write(cat, bad)
    _write(bak, json.dumps(good, ensure_ascii=False))

    c = Catalog(cat)
    assert len(c.products) == 1
    r = c.resolve("Shure", "ULXD4D")
    assert r is not None
    assert r["category"] == "WIRELESS_RX"
    shutil.rmtree(d, ignore_errors=True)


def test_safe_load_skips_restore_on_readonly():
    d = _mkdtemp()
    good = {"products": [{"brand": "X", "model": "Y", "category": "IO"}]}
    bad = '{"products": [{"defer_reason": "坏" 缺引号且缺闭合"}'
    assert _is_invalid(bad)
    cat = os.path.join(d, "eko_catalog.json")
    bak = os.path.join(d, "eko_catalog.json.bak.20260903030303")
    _write(cat, bad)
    _write(bak, json.dumps(good, ensure_ascii=False))
    # 去掉写权限 -> 模拟打包版只读基线
    os.chmod(cat, 0o444)

    try:
        # 只读介质无法回写 -> 直接抛错，不崩、不静默
        with pytest.raises(json.JSONDecodeError):
            safe_load_json(cat)
    finally:
        os.chmod(cat, 0o644)
        shutil.rmtree(d, ignore_errors=True)
