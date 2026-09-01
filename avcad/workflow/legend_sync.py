"""图例库 → 主库反向同步（R10）。

语义（阳哥 2026-08-31 决定）：

1. **图例库 = 真相**：每次人工在第③步确认 / 修改某个型号的端口定义，
   都要反向把「物理端口聚合数 + 同步标记」写回主库 ``eko_catalog.json``。
   因为主库原本是自动从厂商资料提取的（数量可能错），人工确认后主库
   必须跟着图例库一起更新。
2. **非端口类字段保留**：`dsp / proc_func / channels / impedance_ohm /
   power_w / cascade_outs / cascade / speaker_z` 等阻抗/功率/级联字段
   由人工在主库页单独维护，反推**不动**。
3. **覆盖 inputs/outputs**：按图例库 ports 按 ``role`` 聚合：
   - ``inputs``  = Σ(role=in 且 air=false 的 count)
   - ``outputs`` = Σ(role=out 且 air=false 的 count)
   - air=true（无线 RF）端口不计入物理端口
4. **标 ``legend_rev`` + ``synced_at``**：让前端主库卡一眼能看到
   「这个 params 来自图例库 rev N 反推 + 时间戳」。

落盘策略：
- 调用方传入 ``catalog_path``，落盘前先 ``.bak.YYYYMMDDHHMMSS`` 备份。
- 主库更新是**同步落盘**（与图例库 /api/legend handler 同步触发）。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# 主库默认路径：★ 唯一权威是 catalog_resolver.DEFAULT_JSON。
# 不要在这里再拼一遍路径 —— 两个定义迟早漂移（一个改了一个没改，
# 反推就会写到另一份文件上，且不报错）。
# AVCAD_CATALOG 环境变量只用于测试隔离 / 临时切换主库副本。
from avcad.data.catalog_resolver import DEFAULT_JSON as _CATALOG_JSON  # noqa: E402

DEFAULT_CATALOG = Path(os.environ.get("AVCAD_CATALOG") or _CATALOG_JSON)


def resolve_catalog_path(catalog_path=None) -> Path:
    """落盘路径解析：**调用时**才读 DEFAULT_CATALOG，便于测试 monkeypatch。

    （写成 ``def f(path=DEFAULT_CATALOG)`` 的话默认值在 import 时就绑定了，
    monkeypatch 模块属性不会生效 —— 测试就会写到真实主库上。）
    """
    return Path(catalog_path or DEFAULT_CATALOG)


# 反推时**保留**的非端口类字段（人工在主库页单独维护，反推不动）
PRESERVED_KEYS = {
    "dsp", "proc_func", "channels", "impedance_ohm", "power_w",
    "cascade_outs", "cascade", "speaker_z",
}

# 反推时**覆盖**的端口聚合字段（被反推值替换）
PORT_AGG_KEYS = {"inputs", "outputs"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class ReverseResult:
    matched: bool                       # 主库里是否找到对应条目
    product_index: Optional[int]        # 在 products 数组里的 idx（用于前端）
    before_params: dict                 # 原 params（备份 + 给前端 diff 用）
    after_params: dict                  # 反推后 params（写入磁盘的版本）
    backup_path: Optional[str]          # 备份文件绝对路径（首次落盘时生成）


def _port_count(p) -> int:
    """端口 count 字段读取：兼容 dict 与 dataclass (LegendPort)。"""
    if isinstance(p, dict):
        return int(p.get("count") or 0)
    return int(getattr(p, "count", 0) or 0)


def _port_attr(p, key: str):
    """端口字段读取：兼容 dict 与 dataclass。"""
    if isinstance(p, dict):
        return p.get(key)
    return getattr(p, key, None)


def spec_param_keys(category: str) -> set:
    """从规格 yaml **动态**取该类别消费的 params 键。

    包含三部分（与 ``scripts/probe_param_coverage.py`` 的判定口径一致）：
      1. yaml ``params:`` 段声明的键
      2. ``ports_template`` 里 ``count_from:`` 引用的键
      3. ``ports_template`` 里 ``if_feature:`` 引用的键

    ★ 为什么要动态取而不是写死类别白名单：
      反推时若把 ``inputs``/``outputs`` 写进**规格模板根本不读**的类别
      （如 SPEAKER / AMP —— 它们的端口数由 ``channels`` 或固定模板决定），
      这些值在主库里就是「写了没人读」的死数据：图不会因此多画一个口，
      测试也不会报错，属于**静默失效**。``probe_param_coverage.py`` 会把它
      抓成「未归类的参数键」。改成动态取后，新增类别只要在 yaml 里声明了
      ``inputs``/``outputs`` 就自动纳入反推，无需同步改本文件。
    """
    from avcad.model.specs import load_specs
    cat = (category or "").strip().upper()
    if not cat:
        return set()
    spec = (load_specs() or {}).get(cat)
    if spec is None:
        return set()
    keys = set((getattr(spec, "params", None) or {}).keys())
    for tpl in (getattr(spec, "ports_template", None) or []):
        if not isinstance(tpl, dict):
            continue
        for ref in ("count_from", "if_feature"):
            v = tpl.get(ref)
            if v:
                keys.add(str(v))
    return keys


def reverse_params_from_legend(legend, current_params: Optional[dict] = None) -> dict:
    """根据图例库 ``ports`` 反推主库 ``params``。

    Parameters
    ----------
    legend : ``Legend`` / dict 都可以；要有 ``.ports`` / ``.revision`` 字段
    current_params : 主库现状 params；用于保留 PRESERVED_KEYS + 显式剥离 PORT_AGG_KEYS
    """
    # 兼容 dict / dataclass 两种入参
    if isinstance(legend, dict):
        ports = legend.get("ports") or []
        revision = int(legend.get("revision") or 0)
    else:
        ports = getattr(legend, "ports", []) or []
        revision = int(getattr(legend, "revision", 0) or 0)

    # air=true（无线 RF 空气端口）不计入物理 in/out
    in_total = sum(_port_count(p) for p in ports
                   if _port_attr(p, "role") == "in" and not _port_attr(p, "air"))
    out_total = sum(_port_count(p) for p in ports
                    if _port_attr(p, "role") == "out" and not _port_attr(p, "air"))

    # 保留非端口类字段（**仅当原 params 里有该字段时才保留**；不主动注入 None）
    new = dict(current_params or {})
    # PRESERVED_KEYS 不主动 setdefault——避免给原本干净的 params 注入一堆 None
    # （这些字段由人工在主库页单独维护，反推只动 inputs/outputs/legend_rev/synced_at）

    # ★ 端口聚合字段**按类别**写：只有规格模板确实消费 inputs/outputs 才写。
    #   否则（SPEAKER / AMP 等由 channels 或固定模板决定端口的类别）写了也是死数据。
    category = (legend.get("category") if isinstance(legend, dict)
                else getattr(legend, "category", "")) or ""
    consumed = spec_param_keys(category)
    for k, v in (("inputs", in_total), ("outputs", out_total)):
        if k in consumed:
            new.pop(k, None)
            new[k] = int(v)

    new["legend_rev"] = revision
    new["synced_at"] = _now()
    return new


def _norm(s) -> str:
    return (s or "").strip()


def find_product_index(products: list, brand: str, model: str, category: str) -> int:
    """在主库 products 数组里找 (brand, model, category) 完全匹配的下标。

    返回 -1 表示未找到。
    防御：
      - brand/model/category 任一为 None / 空字符串 → 不匹配（避免误覆盖）
      - products 元素不是 dict → 跳过

    ★ 类别漂移回退（2026-09-01）：人工在图例校正页把某型号改成了与主库
      推导值不同的类别时，精确匹配会落空，R10 反推就**静默不生效**（主库
      永远停留在旧的错误端口数上，UI 上却显示「已保存」）。此时若该
      (brand, model) 在主库里只有一条，就认这一条 —— 与
      ``LegendStore.get`` 的回退规则保持一致（多条则仍要求类别精确）。
    """
    b = _norm(brand).upper()
    m = _norm(model).upper()
    c = _norm(category).upper()
    if not b or not m or not c:
        return -1
    for i, p in enumerate(products):
        if not isinstance(p, dict):
            continue
        if (_norm(p.get("brand")).upper() == b
                and _norm(p.get("model")).upper() == m
                and _norm(p.get("category")).upper() == c):
            return i
    same_bm = [i for i, p in enumerate(products)
               if isinstance(p, dict)
               and _norm(p.get("brand")).upper() == b
               and _norm(p.get("model")).upper() == m]
    return same_bm[0] if len(same_bm) == 1 else -1


# 备份保留份数。图例库每确认一次就反推一次主库，不设上限的话
# avcad/data/ 会被 .bak.YYYYMMDDHHMMSS 淹没（实测一轮调试就堆了 10 个）。
MAX_BACKUPS = 5


def _prune_backups(catalog_path: Path, keep: int = MAX_BACKUPS) -> int:
    """删除超出保留份数的旧备份，返回被删数量。"""
    p = Path(catalog_path)
    stem = p.name + ".bak."
    try:
        olds = sorted(
            (f for f in p.parent.iterdir()
             if f.is_file() and f.name.startswith(stem)),
            key=lambda f: f.name,          # 时间戳字典序 = 时间序
            reverse=True,
        )
    except OSError:
        return 0
    removed = 0
    for f in olds[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def backup_catalog(catalog_path: Path, keep: int = MAX_BACKUPS) -> Optional[str]:
    """落盘前先备份原主库，返回备份路径；不存在/读不到返回 None。

    只保留最近 ``keep`` 份——备份是给「改坏了想撤回」用的，留几十份
    反而找不到该恢复哪一个。
    """
    p = Path(catalog_path)
    if not p.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak.{ts}")
    try:
        shutil.copy2(p, bak)
    except Exception:
        return None
    _prune_backups(p, keep=keep)
    return str(bak)


def apply_reverse_to_catalog(legend, catalog_path: Optional[Path] = None) -> ReverseResult:
    """图例库 → 主库反向同步的**唯一入口**。

    步骤：
        1) 读主库 JSON
        2) find_product_index 定位 (brand, model, category) 条目
        3) reverse_params_from_legend 计算新 params
        4) backup_catalog 备份原文件
        5) 写回主库

    Returns ``ReverseResult``：让调用方决定要不要再触发前端 banner 刷新。
    """
    p = resolve_catalog_path(catalog_path)
    if not p.exists():
        return ReverseResult(False, None, {}, {}, None)

    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    products = data.get("products", [])

    brand = (legend.get("brand") if isinstance(legend, dict)
             else getattr(legend, "brand", ""))
    model = (legend.get("model") if isinstance(legend, dict)
             else getattr(legend, "model", ""))
    category = (legend.get("category") if isinstance(legend, dict)
                else getattr(legend, "category", ""))

    idx = find_product_index(products, brand, model, category)
    if idx < 0:
        # 主库里没这条 → 不创建新条目（避免污染原始产品清单）
        return ReverseResult(False, None, {}, {}, None)

    prod = products[idx]
    before = dict(prod.get("params") or {})
    after = reverse_params_from_legend(legend, current_params=before)

    # 备份 → 写盘
    bak = backup_catalog(p)
    prod["params"] = after

    # 原子写：先临时文件再 os.replace
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

    return ReverseResult(
        matched=True,
        product_index=idx,
        before_params=before,
        after_params=after,
        backup_path=bak,
    )


__all__ = [
    "DEFAULT_CATALOG",
    "PRESERVED_KEYS",
    "PORT_AGG_KEYS",
    "ReverseResult",
    "resolve_catalog_path",
    "spec_param_keys",
    "reverse_params_from_legend",
    "find_product_index",
    "backup_catalog",
    "apply_reverse_to_catalog",
    "apply_reverse_all",
]


def apply_reverse_all(legend_store=None, catalog_path: Optional[Path] = None,
                      only_matched: bool = True) -> List[ReverseResult]:
    """批量反推：把图例库所有 entry 反向同步到主库。

    用于「首次启用」R10 时一次性把现有图例库同步到主库；
    之后正常流程是 /api/legend PUT 时逐条反推（apply_reverse_to_catalog）。

    Parameters
    ----------
    legend_store : ``LegendStore`` 实例；None 时新建（用默认路径）
    catalog_path : 主库路径
    only_matched : True（默认）= 只返回匹配上的；False = 返回全部（含未匹配的占位）

    Returns : ``[ReverseResult, ...]``
    """
    if legend_store is None:
        from avcad.workflow.legend_store import LegendStore
        legend_store = LegendStore()
    results: List[ReverseResult] = []
    for lg in legend_store.all():
        rr = apply_reverse_to_catalog(lg, catalog_path=catalog_path)
        if rr.matched or not only_matched:
            results.append(rr)
    return results


# ============================================================
# CLI：首次启用时手动触发一次性反推
# ============================================================
if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(
        description="图例库 → 主库反向同步工具（R10）",
        epilog="首次启用：python -m avcad.workflow.legend_sync --reverse-all"
    )
    ap.add_argument("--reverse-all", action="store_true",
                    help="把图例库所有 entry 一次性反推到主库")
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG,
                    help="主库 JSON 路径（默认 eko_catalog.json）")
    args = ap.parse_args()
    if args.reverse_all:
        rs = apply_reverse_all(catalog_path=args.catalog)
        matched = sum(1 for r in rs if r.matched)
        print(f"[legend_sync] 已反推 {matched}/{len(rs)} 条")
        for r in rs:
            if r.matched:
                p = r.after_params
                print(f"  ✓ rev={p.get('legend_rev')} inputs={p.get('inputs')} "
                      f"outputs={p.get('outputs')}")
    else:
        ap.print_help()
