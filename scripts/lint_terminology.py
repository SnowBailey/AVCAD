#!/usr/bin/env python3
"""术语一致性 linter：扫描 markdown 文档，对照 AVCAD 权威常量做术语一致性检查。

规划 §7.2 交付物 34。零侵入——只读文档、不改任何文件；所有「权威术语」都从
avcad 源码动态取，避免再出现「白名单没跟上枚举」这类名单不同步的坑。

检查两类问题（都刻意做成低噪声，不当成字符串拼写警察）：

  1. 别名 → 权威词：文档里用了已知别名（如旧码 `SPOF`、信号 `AES3`、
     中文冗余写法 `处理器主备`），提示应改成权威写法。别名表 `ALIASES`
     是人工精选、可随项目扩展，**只收录「确实会造成歧义 / 已被重构替换」的写法**，
     不收录品牌常规大小写（如 prose 里的 "Dante" 不强制成 "DANTE"）。

  2. 同一术语大小写不一致：某个权威词在语料里同时以两种大小写出现
     （如一处 `DANTE` 一处 `dante`，或 `PROCESSOR` 与 `processor` 混用），
     报「不一致」。**仅当确实出现 ≥2 种写法才报**——通篇只用 "Dante" 的不报，
     避免 prose 噪声。

用法：
    python3 scripts/lint_terminology.py                 # 扫描 docs/ + av-kb-research/
    python3 scripts/lint_terminology.py --dirs docs      # 只扫 docs/
    python3 scripts/lint_terminology.py --strict         # 发现不一致则退出码 1
    python3 scripts/lint_terminology.py --root /path/to/repo

退出码：0 = 无不一致（或仅 advisory）；1 = 发现不一致且 --strict；2 = 环境错误。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict


# ---------------------------------------------------------------- 别名表
# 仅收录「会造成歧义 / 已被重构替换」的写法。品牌常规大小写（Dante）不在此列。
ALIASES = {
    # 近期重构：旧单码 SPOF 拆成网络/链路两码（2026-09-02）
    "SPOF": "SPOF_NET_SHARED_SWITCH / SPOF_DSP_SINGLE（2026-09-02 已拆分）",
    # 信号别名
    "AES3": "AES",
    "AES/EBU": "AES",
    "AES-EBU": "AES",
    "WORD CLOCK": "WCLK",
    "WCLOCK": "WCLK",
    "WORDCLK": "WCLK",
    # 冗余级别中文写法（清单里 legit，文档里应优先用枚举名）
    "处理器冗余": "PROCESSOR_BACKUP",
    "处理器主备": "PROCESSOR_BACKUP",
    "设备冗余": "DEVICE_BACKUP",
    "设备主备": "DEVICE_BACKUP",
    "链路冗余": "LINK_BACKUP",
    "双链路": "LINK_BACKUP",
    "全链路冗余": "FULL_CHAIN",
}


def load_canonical_terms(repo_root):
    """从 avcad 源码动态取权威术语；取不到则用内嵌兜底，保证脚本可独立运行。"""
    signals, categories, redundancies, issue_codes = set(), set(), set(), set()
    fallback = False
    try:
        sys.path.insert(0, repo_root)
        from avcad.model.schema import Signal, Redundancy  # noqa: E402
        from avcad.model.specs import load_specs          # noqa: E402
        signals = set(Signal.__members__)
        redundancies = set(Redundancy.__members__)
        categories = set(load_specs())
        # Issue 码：从 checks.py 正则提取（与 probe_issue_coverage 同源，避免硬编码）
        checks_py = os.path.join(repo_root, "avcad", "validate", "checks.py")
        src = open(checks_py, encoding="utf-8").read()
        issue_codes = set(re.findall(
            r'Issue\(\s*["\'][A-Z]+["\']\s*,\s*["\']([A-Z_]+)["\']', src))
    except Exception:  # noqa: BLE001
        fallback = True
        signals = {"XLR", "AES", "DANTE", "RS232", "RF", "SPEAKER", "IP",
                   "GPIO", "POWER", "OPTICAL", "TRS", "CONF", "USB", "LINK",
                   "WCLK"}
        redundancies = {"NONE", "DEVICE_BACKUP", "PROCESSOR_BACKUP",
                        "LINK_BACKUP", "FULL_CHAIN"}
        categories = {"SOURCE", "MIXER", "PROCESSOR", "AMP", "SPEAKER",
                      "SWITCH", "WIRELESS_RX", "WIRELESS_MIC", "MIC_HOST",
                      "ANT_DIST", "SPEAKER_MGR", "IO"}
    return signals, categories, redundancies, issue_codes, fallback


def scan_files(root, dirs):
    """收集待扫描的 .md 文件（排除 node_modules / .git 等）。"""
    out = []
    for d in dirs:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for cur, _dirs, files in os.walk(base):
            _dirs[:] = [x for x in _dirs if x not in (".git", "node_modules",
                                                       "__pycache__")]
            for f in files:
                if f.lower().endswith(".md"):
                    out.append(os.path.join(cur, f))
    return sorted(out)


def _is_word_char(ch):
    return bool(ch) and (ch.isalnum() or ch == "_")


def check_aliases(text):
    """返回本文档命中的别名列表 [(alias, suggestion, offset)]。

    ★ 防误报：别名可能本身是更长规范词的前缀（如旧码 `SPOF` 是
    `SPOF_NET_SHARED_SWITCH` / `SPOF_DSP_SINGLE` 的前缀）。用「前后字符非单词字符」
    守卫，只在独立成词时命中——`SPOF_NET_*` 里的 `SPOF` 因紧跟 `_` 被跳过，
    不计入。否则光是报告里引用新码就会虚报十几处。
    """
    hits = []
    for alias, canon in ALIASES.items():
        start = 0
        L = len(alias)
        while True:
            i = text.find(alias, start)
            if i < 0:
                break
            before = text[i - 1] if i > 0 else ""
            after = text[i + L] if i + L < len(text) else ""
            if _is_word_char(before) or _is_word_char(after):
                start = i + L
                continue
            hits.append((alias, canon, i))
            start = i + L
    return hits


def check_case_consistency(text, terms):
    """返回本文档中『大小写不一致的权威词』{term: set(distinct casings)}。"""
    found = defaultdict(set)
    for t in terms:
        for m in re.finditer(r'\b' + re.escape(t) + r'\b', text, re.IGNORECASE):
            found[t].add(m.group(0))
    # 仅保留确实出现 ≥2 种写法的词
    return {t: casings for t, casings in found.items() if len(casings) > 1}


def main():
    ap = argparse.ArgumentParser(description="AVCAD 术语一致性 linter")
    ap.add_argument("--root", default=None, help="仓库根目录（默认=脚本上级目录）")
    ap.add_argument("--dirs", nargs="*", default=["docs", "av-kb-research"],
                    help="要扫描的子目录（相对 root），默认 docs + av-kb-research")
    ap.add_argument("--strict", action="store_true",
                    help="发现不一致时退出码置 1")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))
    signals, categories, redundancies, issue_codes, fallback = \
        load_canonical_terms(root)
    canonical = signals | categories | redundancies | issue_codes

    files = scan_files(root, args.dirs)
    if not files:
        print(f"未找到待扫描的 .md 文件（root={root}, dirs={args.dirs}）")
        return 0

    alias_findings = []   # (rel, alias, canon, line)
    case_findings = []    # (rel, term, casings, line)
    per_file_case = defaultdict(dict)

    for path in files:
        rel = os.path.relpath(path, root)
        try:
            text = open(path, encoding="utf-8").read()
        except Exception as e:  # noqa: BLE001
            print(f"  ! 读取失败 {rel}: {e}")
            continue
        # 别名
        for alias, canon, off in check_aliases(text):
            line = text.count("\n", 0, off) + 1
            alias_findings.append((rel, alias, canon, line))
        # 大小写一致性
        inc = check_case_consistency(text, canonical)
        if inc:
            per_file_case[rel] = inc
            for term, casings in inc.items():
                # 找该词首次出现行号
                m = re.search(r'\b' + re.escape(term) + r'\b',
                              text, re.IGNORECASE)
                line = text.count("\n", 0, m.start()) + 1 if m else 0
                case_findings.append((rel, term, sorted(casings), line))

    total = len(alias_findings) + len(case_findings)
    print("=" * 72)
    print(f"术语一致性扫描（{len(files)} 个 .md 文件）")
    if fallback:
        print("  ⚠ avcad 未能导入，已用内嵌兜底术语集（结果可能不全）")
    print("=" * 72)

    if alias_findings:
        print(f"\n[1] 别名 → 权威词（{len(alias_findings)} 处）")
        for rel, alias, canon, line in alias_findings:
            print(f"  · {rel}:{line}  「{alias}」→ 建议改为「{canon}」")
    else:
        print("\n[1] 别名检查：未发现已知别名写法")

    if case_findings:
        print(f"\n[2] 同一术语大小写不一致（{len(case_findings)} 处）")
        for rel, term, casings, line in case_findings:
            print(f"  · {rel}:{line}  「{term}」出现多种写法：{', '.join(casings)}"
                  f" —— 请统一为权威大小写")
    else:
        print("\n[2] 大小写一致性：所有权威词大小写统一")

    print("\n" + "=" * 72)
    if total == 0:
        print("✓ 未发现术语不一致。")
        return 0
    print(f"⚠ 共发现 {total} 处术语不一致（advisory）。")
    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
