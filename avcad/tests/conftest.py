"""测试全局守卫：任何测试都不得改动仓库里的真实数据文件。

背景（R10 开发时踩的坑，2026-09-01）：
``/api/legend`` PUT 会写两个文件——图例库（永久文档）和主库（R10 起反向同步）。
测试直接调真实 handler，只要漏 ``monkeypatch`` 其中一个路径，跑完一轮测试
``git status`` 就会冒出莫名其妙的改动，而且**测试全绿**，极难发现。

各测试模块自己 monkeypatch 是「第一道防线」，但靠人记得写；
这里加一道**总闸**：会话开始记 md5，会话结束比对，谁写坏了立刻炸出来，
并直接点名是哪个文件、该 monkeypatch 哪个常量。

⚠️ 2026-09-03 重写：旧版只在**会话结束**比对 md5（事后检测），不拦截写盘——
测试一跑真实文件就被改落盘，且会话中途中断（``-x`` / Ctrl-C / CI 部分失败）
比对根本不执行 → 静默污染。新版改为**两道防线**：
  ① 拦截：全局 ``os.replace`` 落盘点重定向——只有当 ``dst`` 解析后**恰好等于真实数据路径**
     时才重定向到临时影子副本，真实文件在测试期间**永不被写**（源头杜绝）。
     ★ 关键：只拦 ``os.replace`` 这一**操作系统层**落盘点，**绝不**去改
     ``resolve_catalog_path`` / ``_resolve_legend_path`` / ``_CATALOG_PATH`` 这些解析器/别名——
     否则会误伤那些「自己用 monkeypatch 把落盘路径指到临时目录、并要验证写入生效」的测试
     （如 test_catalog_path_resolved_lazily、test_reverse_writes_when_catalog_writable）。
     某条写盘入口走哪条解析器，是测试自己的事；本总闸只看「最终落盘路径是不是真实文件」。
  ② 兜底：会话结束比对 md5，若仍有绕过①的写盘（如直接 open(real,'w')），
     自动还原真实文件并炸出，点名该 monkeypatch 的常量。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
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


@pytest.fixture(autouse=True)
def _fresh_entry_cache():
    """每个用例前清空 ``app._ENTRY_CACHE``。

    ★ 2026-09-01 R12 踩的坑：``_ENTRY_CACHE`` 是模块级、按 BOM 文本 sha1 缓存的
      **解析结果**，而解析结果取决于``当时``生效的主库（测试用 monkeypatch 换库）。
      跨用例不清 → 前一个用例在空主库下解析出的条目（类别被兜底成 IO）
      会被后一个用例直接命中缓存复用，哪怕后者换了一份含该型号的主库。
      表现是「单跑绿、整轮跑红」，且报错信息（category='IO'）指向完全错误的方向。
    """
    import avcad.ui.app as _app
    _app._ENTRY_CACHE.clear()
    yield
    _app._ENTRY_CACHE.clear()


@pytest.fixture(scope="session", autouse=True)
def _no_real_data_writes():
    # 注意：monkeypatch fixture 是 function-scoped，不能用于 session 级 fixture，
    # 这里改用 pytest.MonkeyPatch() 实例手动打补丁，teardown 时 undo()。
    mp = pytest.MonkeyPatch()
    repo = Path(__file__).resolve().parents[2]
    # 真实文件的绝对路径 + 原始字节（用于兜底还原）
    real = {}
    for rel in GUARDED:
        f = repo / rel
        if f.exists():
            real[rel] = f.resolve()
    before = {f: (f.read_bytes(), _md5(f)) for f in real.values()}

    # ── 第一道防线：拦截写盘，真实文件永不被碰 ──────────────────────────────
    tmp_dir = Path(tempfile.mkdtemp(prefix="avcad_test_data_"))
    tmp_map = {}  # 真实绝对路径 -> 临时副本路径
    for rel, rp in real.items():
        tp = tmp_dir / Path(rel).name
        tp.write_bytes(before[rp][0])  # 用真实内容播种，保证读写一致
        tmp_map[rp] = tp

    if tmp_map:
        # 只拦操作系统层 ``os.replace`` 的落盘点：
        # 当且仅当 dst 解析后 == 真实数据文件绝对路径时，重定向到临时影子副本。
        # 解析器（resolve_catalog_path / _resolve_legend_path）和模块级别名（_CATALOG_PATH）
        # 一律不动——测试自己把落盘路径 monkeypatch 到临时目录时，dst 是临时路径，
        # 自然落在本总闸之外，验证写入生效的测试不会被误伤。
        _real_replace = os.replace
        _shadow = dict(tmp_map)  # 真实绝对路径 -> 影子副本路径

        def _guarded_replace(src, dst, *a, **k):
            dp = Path(dst).resolve()
            if dp in _shadow:
                return _real_replace(src, str(_shadow[dp]), *a, **k)
            return _real_replace(src, dst, *a, **k)

        mp.setattr(os, "replace", _guarded_replace)

    try:
        yield
    finally:
        mp.undo()

    # ── 第二道防线：兜底检测 + 自动还原 ──────────────────────────────────────
    dirty = []
    for f, (orig_bytes, md5_before) in before.items():
        if not f.exists():
            dirty.append(f"{f}（被删除！）")
            continue
        if _md5(f) != md5_before:
            f.write_bytes(orig_bytes)  # 自动还原，避免污染进仓库
            rel = str(f.relative_to(repo))
            dirty.append(
                f"{rel}  ← 测试写坏了（已自动还原）。该写盘入口未过拦截，"
                f"请 monkeypatch `{GUARDED[rel]}` 或 _resolve_legend_path / resolve_catalog_path"
            )
    if dirty:
        raise AssertionError(
            "测试污染了仓库里的真实数据文件（已自动还原）：\n  - " + "\n  - ".join(dirty)
        )
