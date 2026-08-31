"""覆盖度探针统一入口：一条命令跑完三套探针。

三套探针是**同一个方法论**的三个切面，都是回答一个问题：
「这段逻辑/这批数据，有没有被任何真实场景走到过？」

  probe_link_coverage  连线函数：零命中 = 该分支从未被真实数据执行，缺陷潜伏
  probe_issue_coverage 校验 Issue 码：零命中 = 系统一直合规 **或** 该报没报
  probe_param_coverage 主库参数键：写了但模板不认 = 配置静默失效

命中的共同教训（2026-08-31，一天内靠这三套探针挖出 4 个真缺陷）：
  * 真实清单 10 方案 × 8 遍全绿 **不等于** 代码正确，只说明这条路径没被走到
    （会讨无线链路藏了 1 fatal + 3 处画错，全靠构造样例场景才暴露）
  * 零命中要**人工构造「必然违规」场景**区分「一直合规」与「该报没报」
  * 验证结论必须**写进脚本常量**，否则下次又会从头排查一遍

用法：
    python3 scripts/probe_all.py            # 跑全部
    python3 scripts/probe_all.py link       # 只跑指定探针（link/issue/param）
退出码：任一探针返回非 0 则为 1（便于 CI 接入）
"""
from __future__ import annotations
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

PROBES = [
    ("link", "probe_link_coverage.py", "连线函数覆盖度"),
    ("issue", "probe_issue_coverage.py", "校验 Issue 码覆盖度"),
    ("param", "probe_param_coverage.py", "主库参数键覆盖度"),
]


def run(key: str, script: str, title: str) -> int:
    path = os.path.join(HERE, script)
    print(f"\n{'#'*78}\n# {title}  （{script}）\n{'#'*78}")
    if not os.path.exists(path):
        print(f"  ! 脚本不存在：{path}")
        return 1
    r = subprocess.run([PY, path], cwd=os.path.dirname(HERE))
    return r.returncode


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only and not any(k == only for k, _, _ in PROBES):
        print(f"未知探针：{only}（可选：{' / '.join(k for k, _, _ in PROBES)}）")
        return 1

    results = []
    for key, script, title in PROBES:
        if only and key != only:
            continue
        results.append((key, run(key, script, title)))

    print(f"\n{'='*78}\n汇总\n{'='*78}")
    failed = []
    for key, rc in results:
        flag = "✓" if rc == 0 else "⚠"
        print(f"  {flag} {key:<8} 退出码 {rc}")
        if rc:
            failed.append(key)
    if failed:
        print(f"\n存在需要关注的探针：{', '.join(failed)}")
        print("（探针返回非 0 表示「有零命中/未归类项」，不等于测试失败——"
              "按各自脚本的提示处置即可）")
        return 1
    print("\n✓ 三套探针均无待处置项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
