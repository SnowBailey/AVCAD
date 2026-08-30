#!/usr/bin/env python3
"""交互页面端到端稳定性验证：完整 5 步管线循环 8 次。

每次均断言：解析成功 / 架构有推荐 / 生成 SVG 非空 / 排版校验通过(overlap=0,diagonal=0)
/ DXF 导出非空 / 结果可复现（8 次 overlap/diagonal 一致）。

用法：python scripts/verify_ui_8x.py
"""
from __future__ import annotations
import base64
import json
import sys
import time
from pathlib import Path

# 将仓库根目录加入路径（脚本位于 <repo>/scripts/）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import avcad.ui.app as app

XLSX = "/Users/mac/Desktop/测试.xlsx"
N = 8


def _call(path, body):
    return app._dispatch(path, json.dumps(body or {}))


def main():
    raw = open(XLSX, "rb").read()
    b64 = base64.b64encode(raw).decode()
    print(f"稳定验证：真实清单 测试.xlsx 完整管线 × {N}\n")

    # 步骤1：解析一次（xlsx 仅解析一次，后续复用 CSV —— 速度优化核心）
    p = _call("/api/parse", {"filename": "测试.xlsx", "b64": b64})
    assert p["csv"] and p["modules"], "parse failed"
    print(f"  解析: {len(p['modules'])} 模块, 排除 {len(p['dropped'])} 项: {p['dropped']}")

    results = []
    t_total = 0.0
    for i in range(1, N + 1):
        t0 = time.perf_counter()
        # 步骤4：架构（含冗余切换）
        red = "FULL_CHAIN" if i % 2 == 0 else "PROCESSOR_BACKUP"
        arch = _call("/api/architectures", {"bom": p["csv"], "redundancy": red})
        # 步骤5：生成 + 校验
        run = _call("/api/run", {"bom": p["csv"], "decisions": {},
                                 "redundancy": red, "name": "稳定验证"})
        # 导出
        exp = _call("/api/export", {"bom": p["csv"], "redundancy": red, "name": "稳定验证"})
        dt = (time.perf_counter() - t0) * 1000
        t_total += dt

        ok = (
            run["svg"] and run["validation"]["ok"]
            and run["validation"]["overlap"] == 0
            and run["validation"]["diagonal"] == 0
            and len(exp["dxf_b64"]) > 1000
            and arch["architectures"]
        )
        results.append((run["validation"]["overlap"], run["validation"]["diagonal"], len(exp["dxf_b64"])))
        status = "PASS ✅" if ok else "FAIL ❌"
        print(f"  轮 {i}: overlap={run['validation']['overlap']} diagonal={run['validation']['diagonal']} "
              f"dxf={len(exp['dxf_b64'])}B arch={arch['architectures'][0]['id']} "
              f"引擎={run.get('build_ms')}ms 墙钟={dt:.1f}ms  {status}")
        assert ok, f"第 {i} 轮未通过"

    # 复现性：8 次 overlap/diagonal 必须一致
    assert len({r[0] for r in results}) == 1, "overlap 不一致"
    assert len({r[1] for r in results}) == 1, "diagonal 不一致"
    print(f"\n复现性: 8 轮 overlap/diagonal 完全一致 ✅")
    print(f"平均端到端耗时: {t_total / N:.1f} ms/轮（含架构+生成+导出）")
    print(f"\n全部 {N} 轮验证通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
