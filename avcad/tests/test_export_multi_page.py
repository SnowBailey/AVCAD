"""R_fix 守卫生效：「快速下载 DXF」多页一次下全部。

2026-09-04 阳哥实测：xlsx 多页清单时点「快速下载 DXF」，浏览器只下 1 张
（对应当前激活页），而非全部 xlsx 工作表各 1 张。根因：
  - 前端 ``index.html`` ``$("btnExport").addEventListener`` 里只调
    ``/api/export`` 并送 ``activePageCsv()``（当前页 CSV），一次只出 1 张
  - 后端 ``app.py`` 当时没有多页导出入口；没有 ``zipfile`` / ``io`` import
  - 浏览器对多次自动 download 会拦截：必须后端打成 zip 一次给到

本测试同时盯后端路由 + 前端分支，防两边任一回归。

失败示例：
  - 把 ``if path == "/api/export-all"`` 删了 → 路由测试挂
  - 把 btnExport 改回只走 /api/export → 前端测试挂
  - 删 ``import zipfile`` → 路由 + 退化测试挂（首次 POST 触发 NameError）
"""
from __future__ import annotations
import base64
import io
import re
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP_PY = REPO / "avcad" / "ui" / "app.py"
INDEX = REPO / "avcad" / "ui" / "static" / "index.html"


# ───────────────── 后端层 ─────────────────

def _app_src() -> str:
    assert APP_PY.exists(), f"缺后端源：{APP_PY}"
    return APP_PY.read_text("utf-8")


def test_app_imports_zipfile_and_io():
    """/api/export-all 走 ``zipfile`` + ``io`` 内存打包，缺其一启动就 NameError。"""
    src = _app_src()
    assert re.search(r"^import\s+zipfile\b", src, re.MULTILINE), (
        "app.py 缺 'import zipfile'；/api/export-all 会 NameError"
    )
    assert re.search(r"^import\s+io\b", src, re.MULTILINE), (
        "app.py 缺 'import io'；/api/export-all 用 io.BytesIO() 会 NameError"
    )


def test_api_export_all_handler_routed():
    """do_POST 必须显式路由 /api/export-all 到 _api_export_all。"""
    src = _app_src()
    assert re.search(
        r'if\s+path\s*==\s*"/api/export-all"\s*:', src
    ), "do_POST 里少了 '/api/export-all' 路由分发"
    assert re.search(
        r'_api_export_all\s*\(\s*json\.loads\s*\(\s*body', src
    ), "/api/export-all 没有调用 _api_export_all(json.loads(body))"


def test_export_all_function_defined_and_returns_zip_b64():
    """_api_export_all 必须定义，且返回 zip_b64 字段（多页一次性 zip）。"""
    src = _app_src()
    assert "def _api_export_all" in src, (
        "_api_export_all 函数丢了；/api/export-all 路由会 NameError"
    )
    assert re.search(r'_api_export_all[\s\S]+?"zip_b64"', src), (
        "_api_export_all 必须返回字段 'zip_b64'（与 /api/export 的 dxf_b64 对齐）"
    )
    # 多页退化：pages 缺省时仍要能出 zip（即使只有 1 张 dxf），不让前端崩溃
    assert re.search(r"if\s+not\s+pages\s*:", src), (
        "_api_export_all 必须有 'if not pages:' 单页退化，否则单页 BOM 调它会出错"
    )


def test_export_all_handler_live_call_multipage():
    """端到端：直接 import app 模块 + POST /api/export-all 双页，返回 zip 内含
    ≥1 个 .dxf，且 _safe_filename 风格去重正确（不强求每页 1 个，避免 zipfile
    退化为 0 entry 时被跳过）。

    注意：未启动 HTTP server，调用 ``Handler.do_POST`` 等价路径需要实例化。
    但 ``_api_export_all`` 是模块级纯函数，直接调即可，省掉 HTTP 复杂度。
    """
    import sys
    sys.path.insert(0, str(REPO))
    import avcad.ui.app as app
    # ★ 满足 _build_project 的最小 contract：bom 一个 SOURCE + 一组 SPEAKER+AMP
    sbom = (
        "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源,电气\n"
        "SPEAKER,IPS,ML210,左右全频线阵列扬声器,1,,power_w=600;impedance_ohm=8;legend_rev=6,synced_at=2026-09-01T09:47:10+08:00,,,,\n"
        "AMP,ezacoustics,EM30D,数字功率放大器,1,analog;control,channels=2;power_w_per_ch=900;impedance_ohm=8;legend_rev=6,synced_at=2026-09-01T09:47:11+08:00,,,,\n"
        "PROCESSOR,IPS,GMN1208D,音频处理器,1,analog;control;dante,inputs=8;outputs=8;legend_rev=23;synced_at=2026-09-04T22:12:28+08:00,,,,\n"
    )
    pages = [
        {"csv": sbom, "name": "会议室A"},
        {"csv": sbom, "name": "会议室B"},
    ]
    res = app._api_export_all({"name": "测试工程", "pages": pages})
    assert not isinstance(res, dict) or "error" not in res, (
        f"_api_export_all 失败：{res.get('error') if isinstance(res, dict) else res}"
    )
    assert res["count"] == 2
    raw = base64.b64decode(res["zip_b64"])
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    assert len(names) == 2, f"期望 zip 含 2 个 dxf，实际 {names}"
    for n in names:
        assert n.lower().endswith(".dxf"), f"zip 内非 dxf 文件：{n}"
        # 校验 dxf 是有效的 ezdxf 输出（最小验证：能 zipfile.read + 含 DXF EOF）
        dxf_bytes = zf.read(n)
        assert b"EOF" in dxf_bytes, f"{n} 不是 DXF（缺 EOF 标记）"


# ───────────────── 前端层 ─────────────────

def _index_src() -> str:
    assert INDEX.exists(), f"缺前端源：{INDEX}"
    return INDEX.read_text("utf-8")


def _btn_export_listener(src: str) -> str:
    """切出 ``$("btnExport").addEventListener("click", ... ) {...}`` 体内字符串。
    用括号深度配对法取首个闭合 ``}``。"""
    m = re.search(
        r'\$\(\s*"btnExport"\s*\)\.addEventListener\(\s*"click"\s*,\s*async\s*\(\)\s*=>\s*\{',
        src)
    assert m, "源码里 $('btnExport').addEventListener(...) 起点找不到"
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{": depth += 1
        elif c == "}": depth -= 1
        i += 1
    assert depth == 0, "$('btnExport') listener 括号未闭合"
    return src[start:i - 1]


def test_btn_export_routes_to_export_all_when_multi():
    """``btnExport`` listener 内必须有「pages.length > 1 → /api/export-all」分支。"""
    src = _index_src()
    body = _btn_export_listener(src)
    # JS 对象字面量 key 可以不带引号（shorthand），所以只匹标识符 ``pages``
    assert re.search(r'\bpages\b', body), (
        "btnExport listener 里没出现 pages 标识符（已被掏空回单页路径）"
    )
    assert "/api/export-all" in body, (
        "btnExport listener 没调 /api/export-all；多页时仍走 /api/export → "
        "继续只下当前激活页那 1 张（阳哥 2026-09-04 实测）"
    )
    assert "zip_b64" in body, (
        "btnExport 多页分支没读 res.zip_b64 解 base64；应该是 zip 包"
    )
    assert re.search(r'pages\.length\s*>\s*1', body), (
        "btnExport 多页分支没以 'pages.length > 1' 作门槛（被改回 'pages.length' 无条件多页"
        "会导致单页也走 zip 路径，回归）"
    )


def test_btn_export_single_page_path_preserved():
    """单页路径 ``/api/export`` + ``dxf_b64`` 不能丢——避免误改双单页面流程。"""
    src = _index_src()
    body = _btn_export_listener(src)
    assert "/api/export" in body, (
        "btnExport listener 里 /api/export 调用没了（单页也会走多页分支 → 单页也变 zip，回归）"
    )
    assert "dxf_b64" in body, (
        "btnExport 单页分支没读 res.dxf_b64 解 base64 → 单页下载会失败"
    )
