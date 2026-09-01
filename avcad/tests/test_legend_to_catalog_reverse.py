"""R10 反向同步守卫（阳哥规则 2026-08-31）。

主库要根据图例库更新，图例库每次是最准的。

回归防线（守住即可）：

  · 图例库 ports 反推主库 params 后：
      - inputs  = Σ(role=in 且 air=false 的 count)
      - outputs = Σ(role=out 且 air=false 的 count)
      - air=true（无线 RF）端口不计入物理端口
  · 主库原有非端口类字段（dsp / proc_func / channels / impedance_ohm / power_w
    / cascade_outs / cascade / speaker_z）**必须保留**（不主动注入 None）
  · 主库空 params 不被污染（反推后只剩 inputs / outputs / legend_rev / synced_at）
  · 反推写入磁盘后主库文件 mtime 变化（让 _load_catalog() 缓存失效重读）

为什么重要：
  主库原本是自动从厂商资料提取的（数量可能错）；人工在图例库改完，
  物理端口聚合数必须自动跟改，否则主库仍是旧的、错的。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from avcad.workflow.legend_sync import (
    PORT_AGG_KEYS,
    apply_reverse_to_catalog,
    find_product_index,
    reverse_params_from_legend,
    spec_param_keys,
)


CAT_PATH = Path("avcad/data/eko_catalog.json")


@pytest.fixture
def restore_catalog():
    """每个 case 跑完恢复 eko_catalog.json 到测试前的状态。"""
    bak = CAT_PATH.with_suffix(".json.legend_sync_bak")
    shutil.copy(CAT_PATH, bak)
    yield
    shutil.copy(bak, CAT_PATH)
    bak.unlink(missing_ok=True)


# ============================================================
# 单元层：reverse_params_from_legend（不落盘）
# ============================================================

def test_inputs_outputs_basic():
    """基础反推：XLR in×12 + XLR out×8 + DANTE out×2 → inputs=12, outputs=10。"""
    lg = {
        "brand": "IPS", "model": "TEST_R10_1", "category": "PROCESSOR",
        "revision": 7,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 12, "air": False},
            {"signal": "XLR", "role": "out", "count": 8, "air": False},
            {"signal": "DANTE", "role": "out", "count": 2, "air": False},
        ],
    }
    r = reverse_params_from_legend(lg, current_params={})
    assert r["inputs"] == 12
    assert r["outputs"] == 10            # 8 + 2，**DANTE out 计入物理输出**
    assert r["legend_rev"] == 7
    assert r["synced_at"]


def test_air_port_not_counted():
    """air=true（无线 RF 空气端口）不计入物理 inputs / outputs。

    用 ANT_DIST（消费 inputs/outputs 的类别）承载这个场景——
    WIRELESS_MIC 规格模板不认 inputs/outputs，反推会整体跳过端口聚合。
    """
    lg = {
        "brand": "SHURE", "model": "TEST_R10_2", "category": "ANT_DIST",
        "revision": 3,
        "ports": [
            {"signal": "RF", "role": "out", "count": 1, "air": True},
            {"signal": "XLR", "role": "out", "count": 4, "air": False},
        ],
    }
    r = reverse_params_from_legend(lg, current_params={})
    assert r["outputs"] == 4             # air RF 不计入
    assert r["inputs"] == 0


def test_preserves_non_port_fields():
    """主库原有 dsp / impedance_ohm / cascade 等**必须保留**（不注入 None）。"""
    lg = {
        "brand": "BSS", "model": "TEST_R10_3", "category": "PROCESSOR",
        "revision": 5,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 4, "air": False},
            {"signal": "XLR", "role": "out", "count": 4, "air": False},
        ],
    }
    cur = {
        "inputs": 99, "outputs": 99,            # 旧值要被覆盖
        "dsp": "AES",                           # 必须保留
        "impedance_ohm": 8,                     # 必须保留
        "cascade_outs": 2,                      # 必须保留
    }
    r = reverse_params_from_legend(lg, current_params=cur)
    assert r["dsp"] == "AES"
    assert r["impedance_ohm"] == 8
    assert r["cascade_outs"] == 2
    assert r["inputs"] == 4                    # 被反推覆盖
    assert r["outputs"] == 4
    # 关键：不主动注入空字段
    assert "channels" not in r
    assert "power_w" not in r


def test_empty_params_no_pollution():
    """主库空 params 不被污染（反推后只剩 4 个键）。"""
    lg = {
        "brand": "YAMAHA", "model": "TEST_R10_4", "category": "MIXER",
        "revision": 2,
        "ports": [{"signal": "XLR", "role": "in", "count": 16, "air": False}],
    }
    r = reverse_params_from_legend(lg, current_params={})
    assert set(r.keys()) == {"inputs", "outputs", "legend_rev", "synced_at"}
    assert r["inputs"] == 16
    assert r["outputs"] == 0


# ============================================================
# 按类别动态判定：只写「规格模板确实消费」的端口聚合字段
#
# ★ 阳哥规则 / MEMORY 铁律：出现「新增 X 要同步 N 处」的地方 → 变成测试。
#   反推若把 inputs/outputs 写进规格模板不读的类别（SPEAKER / AMP / WIRELESS_MIC
#   / SOURCE / ANTENNA / MIC_HOST / SWITCH / WIRELESS_RX），就是「写了没人读」的
#   死数据：图上不会多画一个口，测试也不报错，只有 probe_param_coverage 会抓。
# ============================================================

# 从规格 yaml 动态算出来的「消费 inputs/outputs 的类别」——不写死，防止漂移
def _cats_consuming_port_agg():
    from avcad.model.specs import load_specs
    return {c for c in load_specs()
            if PORT_AGG_KEYS <= spec_param_keys(c)}


def test_spec_param_keys_reads_from_spec_yaml():
    """spec_param_keys 必须来自规格 yaml，不是硬编码名单。"""
    # 消费 inputs/outputs 的类别（yaml params 段声明了）
    for cat in ("MIXER", "PROCESSOR", "IO", "ANT_DIST", "SPEAKER_MGR"):
        assert PORT_AGG_KEYS <= spec_param_keys(cat), f"{cat} 应消费 inputs/outputs"
    # 不消费的类别
    for cat in ("SPEAKER", "AMP", "WIRELESS_MIC", "WIRELESS_RX", "SOURCE",
                "ANTENNA", "MIC_HOST", "SWITCH"):
        assert not (PORT_AGG_KEYS & spec_param_keys(cat)), \
            f"{cat} 规格模板不认 inputs/outputs"
    # 未知类别 / 空类别 → 空集合（不写任何端口聚合）
    assert spec_param_keys("") == set()
    assert spec_param_keys("NO_SUCH_CATEGORY") == set()


def test_no_hardcoded_category_whitelist_in_legend_sync():
    """legend_sync.py 里不许出现硬编码的类别白名单。

    一旦有人为了图省事写 ``if category in {"MIXER", "PROCESSOR"}``，
    新增类别就要同步改两处（yaml + 这里），违反 MEMORY 铁律 → 直接拦下。
    """
    import re
    from pathlib import Path
    src = Path("avcad/workflow/legend_sync.py").read_text(encoding="utf-8")
    # 找集合字面量里出现全大写类别名的写法（形如 {"MIXER", "PROCESSOR"}）
    for m in re.finditer(r"\{[^{}]*\}", src):
        chunk = m.group(0)
        cats = re.findall(r'"([A-Z][A-Z_]{3,})"', chunk)
        if len(cats) >= 2:
            raise AssertionError(
                f"legend_sync.py 疑似出现硬编码类别名单：{chunk}。"
                f"请改用 spec_param_keys(category) 从规格 yaml 动态取"
            )


def test_category_without_port_agg_skips_inputs_outputs():
    """SPEAKER（规格模板不认 inputs/outputs）反推：只写同步标记，不写端口聚合。"""
    lg = {
        "brand": "IPS", "model": "TEST_R10_5", "category": "SPEAKER",
        "revision": 4,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 1, "air": False},
            {"signal": "SPEAKER", "role": "out", "count": 1, "air": False},
        ],
    }
    r = reverse_params_from_legend(lg, current_params={})
    # 不写 inputs/outputs（写了没人读 = 静默失效，probe_param_coverage 会报警）
    assert "inputs" not in r
    assert "outputs" not in r
    # 但同步标记照写：前端要能看到「图例库已确认到 rev N」
    assert r["legend_rev"] == 4
    assert r["synced_at"]


def test_amp_keeps_channels_driven_ports():
    """AMP 的端口数由 channels 决定，反推不得覆盖/注入 inputs/outputs。"""
    lg = {
        "brand": "ezacoustics", "model": "TEST_R10_6", "category": "AMP",
        "revision": 6,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 2, "air": False},
            {"signal": "SPEAKER", "role": "out", "count": 4, "air": False},
        ],
    }
    cur = {"channels": 4, "power_w": 800}
    r = reverse_params_from_legend(lg, current_params=cur)
    assert "inputs" not in r
    assert "outputs" not in r
    assert r["channels"] == 4            # 保留
    assert r["power_w"] == 800           # 保留
    assert r["legend_rev"] == 6


def test_port_agg_categories_matches_spec_yaml():
    """消费 inputs/outputs 的类别集合 = 从 yaml 动态算出（防名单漂移）。

    这条是「总闸」：任何 yaml 新增/删除 inputs/outputs，本测试会立刻指出
    反推覆盖范围变了，逼人工确认是不是有意为之。
    """
    cats = _cats_consuming_port_agg()
    # 当前基线（2026-09-01）：这 5 个类别的规格模板声明了 inputs/outputs
    assert cats == {"MIXER", "PROCESSOR", "IO", "ANT_DIST", "SPEAKER_MGR"}, \
        f"消费 inputs/outputs 的类别变了：{sorted(cats)}。确认是有意改动后请更新本基线"


def test_default_catalog_is_single_source_of_truth():
    """legend_sync.DEFAULT_CATALOG 必须直接引用 catalog_resolver.DEFAULT_JSON。

    两个独立路径定义迟早漂移：改了一个没改另一个，反推就写到另一份文件上，
    而且**不报错** —— 前端看主库没变，实际数据落在别处。
    """
    from avcad.data.catalog_resolver import DEFAULT_JSON
    import avcad.workflow.legend_sync as lsync
    assert Path(lsync.DEFAULT_CATALOG) == Path(DEFAULT_JSON)


def test_catalog_path_resolved_lazily(monkeypatch, tmp_path):
    """落盘路径在**调用时**解析 —— monkeypatch DEFAULT_CATALOG 必须生效。

    写成 ``def apply_reverse(legend, catalog_path=DEFAULT_CATALOG)`` 的话，
    默认值在 import 时就绑定了，monkeypatch 模块属性形同虚设，
    测试会静默写进**真实主库**（R10 开发时就踩过：TF5 被写成 inputs=5）。
    """
    import avcad.workflow.legend_sync as lsync
    iso = tmp_path / "isolated_catalog.json"
    monkeypatch.setattr(lsync, "DEFAULT_CATALOG", iso)
    assert lsync.resolve_catalog_path() == iso

    # 目标文件不存在 -> 不创建、不写盘，返回未匹配（测试隔离靠这条）
    lg = {"brand": "IPS", "model": "GMN1208D", "category": "PROCESSOR",
          "revision": 1, "ports": [{"signal": "XLR", "role": "in", "count": 3,
                                    "air": False}]}
    rr = lsync.apply_reverse_to_catalog(lg)
    assert rr.matched is False
    assert not iso.exists()


# ============================================================
# 集成层：apply_reverse_to_catalog（真实落盘）
# ============================================================

def test_apply_reverse_to_catalog_writes_disk(restore_catalog, tmp_path):
    """反推端到端落盘：主库 GMN1208D params 被更新，备份文件生成。"""
    lg = {
        "brand": "IPS", "model": "GMN1208D", "category": "PROCESSOR",
        "revision": 99,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 12, "air": False},
            {"signal": "XLR", "role": "out", "count": 10, "air": False},     # 改 8→10
            {"signal": "DANTE", "role": "out", "count": 2, "air": False},
        ],
    }
    rr = apply_reverse_to_catalog(lg, CAT_PATH)
    assert rr.matched is True
    assert isinstance(rr.product_index, int)
    assert rr.backup_path is not None
    assert Path(rr.backup_path).exists()

    # 主库确实改了
    after = json.loads(CAT_PATH.read_text(encoding="utf-8"))
    prod = after["products"][rr.product_index]
    assert prod["params"]["inputs"] == 12
    assert prod["params"]["outputs"] == 12   # 10 + 2
    assert prod["params"]["legend_rev"] == 99
    assert "synced_at" in prod["params"]


def test_apply_no_match_does_not_create_product(restore_catalog):
    """主库里没有的型号 → 不创建新条目（避免污染原始产品清单）。"""
    lg = {
        "brand": "FAKE", "model": "DOES_NOT_EXIST", "category": "MIXER",
        "revision": 1,
        "ports": [{"signal": "XLR", "role": "in", "count": 8, "air": False}],
    }
    before_count = len(json.loads(CAT_PATH.read_text(encoding="utf-8"))["products"])
    rr = apply_reverse_to_catalog(lg, CAT_PATH)
    assert rr.matched is False
    after_count = len(json.loads(CAT_PATH.read_text(encoding="utf-8"))["products"])
    assert after_count == before_count


def test_find_product_index_returns_minus_one_for_missing():
    """主库里没有的型号 → 返回 -1。"""
    data = {"products": [
        {"brand": "IPS", "model": "GMN1208D", "category": "PROCESSOR"},
    ]}
    assert find_product_index(data["products"], "FAKE", "X", "MIXER") == -1
    # category 为空也不匹配（防误覆盖）
    assert find_product_index(data["products"], "IPS", "GMN1208D", "") == -1


def test_idempotent_reverse(restore_catalog):
    """相同 legend 多次反推结果幂等（不影响备份以外的主库）。"""
    lg = {
        "brand": "IPS", "model": "GMN1208D", "category": "PROCESSOR",
        "revision": 50,
        "ports": [
            {"signal": "XLR", "role": "in", "count": 12, "air": False},
            {"signal": "XLR", "role": "out", "count": 8, "air": False},
        ],
    }
    rr1 = apply_reverse_to_catalog(lg, CAT_PATH)
    rr2 = apply_reverse_to_catalog(lg, CAT_PATH)
    assert rr1.matched and rr2.matched
    # 两次反推后 params 完全相同（除 synced_at 时间戳）
    after = json.loads(CAT_PATH.read_text(encoding="utf-8"))
    prod = after["products"][rr1.product_index]
    assert prod["params"]["inputs"] == 12
    assert prod["params"]["outputs"] == 8
    assert prod["params"]["legend_rev"] == 50
