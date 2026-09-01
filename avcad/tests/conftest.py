"""测试全局守卫：任何测试都不得改动仓库里的真实数据文件。

背景（R10 开发时踩的坑，2026-09-01）：
``/api/legend`` PUT 会写两个文件——图例库（永久文档）和主库（R10 起反向同步）。
测试直接调真实 handler，只要漏 ``monkeypatch`` 其中一个路径，跑完一轮测试
``git status`` 就会冒出莫名其妙的改动，而且**测试全绿**，极难发现。

各测试模块自己 monkeypatch 是「第一道防线」，但靠人记得写；
这里加一道**总闸**：会话开始记 md5，会话结束比对，谁写坏了立刻炸出来，
并直接点名是哪个文件、该 monkeypatch 哪个常量。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# 真实数据文件 -> 该 monkeypatch 的常量（写坏了照这个改）
GUARDED = {
    "avcad/data/eko_catalog.json":
        "avcad.workflow.legend_sync.DEFAULT_CATALOG",
    "avcad/data/legend_library.json":
        "avcad.workflow.legend_store.DEFAULT_CACHE",
}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _no_real_data_writes():
    repo = Path(__file__).resolve().parents[2]
    before = {}
    for rel in GUARDED:
        f = repo / rel
        if f.exists():
            before[f] = _md5(f)

    yield

    dirty = []
    for f, md5_before in before.items():
        if not f.exists():
            dirty.append(f"{f}（被删除！）")
            continue
        if _md5(f) != md5_before:
            rel = str(f.relative_to(repo))
            dirty.append(
                f"{rel}  ← 测试写坏了。请在该用例里 monkeypatch "
                f"`{GUARDED[rel]}`，否则测试数据会进仓库"
            )
    if dirty:
        raise AssertionError(
            "测试污染了仓库里的真实数据文件：\n  - " + "\n  - ".join(dirty)
        )
