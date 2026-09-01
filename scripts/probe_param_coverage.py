"""主库参数覆盖度探针：找出「写了但规格模板根本不认」的参数键。

背景（2026-08-31）：`probe_link_coverage.py`（连线函数）和 `probe_issue_coverage.py`
（校验码）的思路推广到**数据层**。主库 2325 条里每条都可能带 `params`，但这些键
**没有任何机制保证它被消费**——拼错一个字母、或者模板改了名，配置就**静默失效**：
图上少画端口、连线数不对，而测试全绿、校验也不报。

「被消费」的判定：
  1. 该类别规格 yaml 的 `params:` 段声明的键
  2. yaml 里 `count_from:` / `if_feature:` 引用的键
  3. 代码里跨类别消费的通用键（见 UNIVERSAL）

用法：python3 scripts/probe_param_coverage.py
退出码：0 = 没有「未知」参数键；1 = 存在未登记的键（需要归类或清理）
"""
from __future__ import annotations
import sys
import os
import re
import json
import collections

sys.path.insert(0, ".")

from avcad.model.specs import DATA_DIR  # noqa: E402
from avcad.data.catalog_resolver import DEFAULT_JSON  # noqa: E402

# 跨类别消费的通用参数（在 specs.py / router.py / chain.py / importers.py 里读）
UNIVERSAL = {
    # 端口展开（specs.py expand_instance）
    "slots", "ports_override", "feature_ports",
    # 会讨链路（router.py _conference_link / _conference_box_link）
    "host", "conf_box", "conf_wireless", "conf_chain_max", "conf_link",
    # 套装拆分（importers.py expand_sets）
    "set_expand",
    # 处理器功能判定（chain.py）
    "proc_func", "dsp",
    # 功放/音箱匹配（amp_match.py）
    "power_w", "impedance_ohm", "channels", "power_w_per_ch",
    # 调音台级联（router.py _mixer_cascade）
    "cascade",
    # 天线分配器（router.py _antenna_distribution）
    "cascade_outs", "antennas", "mix_out", "trs_out",
    # 注释/信息，不参与建模
    "note", "active",
}

# 已核实为「信息字段」：给人看的，不参与建模，零命中是预期
KNOWN_INFO = {
    "channels_hint": "通道数提示。功放通道数实际从**清单规格文本**正则提取"
                     "（importers.extract_params），不从主库取；主库这份只作人工参考",
    "true_diversity": "真分集标记。天线口数实际由 `antennas` 控制"
                      "（IPS=4 / AUDIX=2），这个键只是说明",
    "legend_rev": "R10 反向同步标记：图例库 rev N 落盘时自动写入主库 params。"
                  "前端主库卡顶部据此渲染「↘ 由图例 vN 反推」徽章。"
                  "非业务字段，不参与建模",
    "synced_at": "R10 反向同步时间戳：与 legend_rev 配对写入。"
                 "非业务字段，不参与建模",
}

# 已核实为「规范统一前的遗留字段」：应清理主库，保持参数规范统一
# 已清理的遗留字段：主库里已删干净，**命中才报警**。
# 此前放在 DEPRECATED 里无条件打印，清理完后仍每轮输出「命中 0 条」，
# 反而让看报告的人误以为这条还没处理。现在只在有人又加回来时才出声。
CLEANED_UP = {
    "dante_in": "阳哥 2026-08-31 定的参数规范：接口箱只留 `dante_ports`（物理网口数，"
                "默认 1）。Dante 所有通道走一根网线，图上不按通道数画口——"
                "dante_in/dante_out 是规范统一前的遗留，已从主库删除",
    "dante_out": "同上（与 dante_in 成对出现），已从主库删除",
}

# 已知「未建模的能力」：物理上存在但当前不画
KNOWN_UNMODELLED = {
    "bnc": "BNC 口（word clock / AES3 同步）。当前规格模板未建模，"
           "需要时再补端口模板",
}


def yaml_consumed():
    """从规格 yaml 里提取每个类别消费的参数键。"""
    consumed = collections.defaultdict(set)
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith(".yaml"):
            continue
        txt = open(os.path.join(DATA_DIR, fn), encoding="utf-8").read()
        m = re.search(r"^category:\s*(\S+)", txt, re.M)
        if not m:
            continue
        cat = m.group(1)
        pm = re.search(r"^params:\s*\n((?:[ \t]+\S.*\n)+)", txt, re.M)
        if pm:
            for line in pm.group(1).splitlines():
                km = re.match(r"^[ \t]+([A-Za-z_]\w*)\s*:", line)
                if km:
                    consumed[cat].add(km.group(1))
        for v in re.findall(r"count_from:\s*(\w+)", txt):
            consumed[cat].add(v)
        for v in re.findall(r"if_feature:\s*(\w+)", txt):
            consumed[cat].add(v)
    return consumed


def main():
    consumed = yaml_consumed()
    db = json.load(open(DEFAULT_JSON, encoding="utf-8"))
    products = db["products"]

    unknown = collections.Counter()
    examples = collections.defaultdict(list)
    drawn = 0

    for it in products:
        cat = it.get("category")
        if not cat or it.get("no_draw") or cat not in consumed:
            continue
        drawn += 1
        pr = it.get("params") or {}
        if not isinstance(pr, dict):
            continue
        for k in pr:
            if k in consumed[cat] or k in UNIVERSAL:
                continue
            unknown[(cat, k)] += 1
            if len(examples[(cat, k)]) < 3:
                examples[(cat, k)].append(
                    f"{it.get('brand')} {it.get('model')}")

    print(f"主库 {len(products)} 条，其中会出图且有规格模板的 {drawn} 条")
    print(f"\n{'='*74}\n未被规格模板消费的参数键 {len(unknown)} 种\n{'='*74}")

    buckets = {
        "✔ 信息字段（不参与建模，预期）": KNOWN_INFO,
        "· 已知未建模的能力": KNOWN_UNMODELLED,
    }
    revived = []
    stray = []
    for (cat, k), n in unknown.most_common():
        tag = None
        if k in KNOWN_INFO:
            tag = "✔ 信息字段（不参与建模，预期）"
        elif k in KNOWN_UNMODELLED:
            tag = "· 已知未建模的能力"
        elif k in CLEANED_UP:
            tag = None
            revived.append((cat, k, n))
        if tag is None:
            stray.append((cat, k, n))
            continue
        print(f"  {tag[:2]} {cat:<12} {k:<18} ×{n:<4} "
              f"例：{', '.join(examples[(cat, k)])}")

    for title, table in buckets.items():
        print(f"\n  {title}：")
        for k, why in table.items():
            hit = sum(n for (c, kk), n in unknown.items() if kk == k)
            print(f"      {k:<18} 命中 {hit:4d} 条  {why}")

    if revived:
        print(f"\n⚠ 已删除的遗留字段又出现在主库里（{len(revived)} 处）：")
        for cat, k, n in revived:
            print(f"    - {cat}.{k}  ×{n}  例：{', '.join(examples[(cat, k)])}")
            print(f"      {CLEANED_UP[k]}")

    if stray:
        print(f"\n⚠ 以下 {len(stray)} 个参数键未归类，需要判断是拼错还是该补进模板：")
        for cat, k, n in stray:
            print(f"    - {cat}.{k}  ×{n}  例：{', '.join(examples[(cat, k)])}")
        print("\n  处置三选一：① 模板改名/主库改名对齐 ② 确属信息字段 → 加进")
        print("  KNOWN_INFO ③ 确属遗留 → 清理主库并加进 CLEANED_UP。")
        return 1
    if revived:
        return 1
    print("\n✓ 不存在未归类的参数键，也无死灰复燃的遗留字段。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
