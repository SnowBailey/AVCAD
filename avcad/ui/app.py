"""AVCAD 完整交互页面后端（自包含，纯标准库 http.server，无额外依赖）。

完整 5 步工作流 API：
  /api/parse        解析清单（xlsx base64 或 csv 文本）-> 规范化 CSV + 模块清单
  /api/modules      模块清单（兼容）
  /api/legend       图例库（永久文档）get/put/list/confirm
  /api/architectures 参考架构评分排序
  /api/run          端到端生成（SVG + 架构 + 校验 + 用时）
  /api/validate     对 SVG 做重叠/斜线校验
  /api/export       导出 DXF（base64，浏览器直接下载）
  /api/export-save  导出 DXF 并写入用户指定目录（返回落盘绝对路径）
  /api/pick-folder  弹出系统原生「选择文件夹」对话框，返回所选目录
  /api/open-folder  在文件管理器中打开指定目录（Finder / Explorer / xdg-open）

速度优化：
  - xlsx 仅在 /api/parse 解析一次，前端复用规范化 CSV 走后续所有步骤。
  - 进程内 parse_bom 结果按内容哈希缓存，重复生成/改冗余不重复解析与主库补全。
  - 启用 ezdxf C 扩展加速 DXF 写出。
"""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import ezdxf  # C 扩展在可用时自动启用，无需手动开启

from avcad.parse.bom_parser import parse_bom
from avcad.core.build import build_project, _apply_redundancy
from avcad.model.schema import redundancy_levels
from avcad.render.draw import draw_devices, draw_wires, draw_ports, draw_wire_legend
from avcad.render.primitives import Canvas
from avcad.render.svg_render import render_svg
from avcad.render.dxf_render import render_dxf
from avcad.workflow.module_confirm import build_module_list, confirm_modules
from avcad.workflow.run import run_workflow, summarize
from avcad.workflow.legend_store import LegendStore, Legend, LegendPort
from avcad.workflow.legend_builder import (  # 图例校正页 + 图例初值：引擎推断
    infer_from_product, infer_from_entry)
from avcad.workflow.legend_sync import (  # R10 反向同步：图例库 -> 主库
    apply_reverse_to_catalog, resolve_catalog_path, backup_catalog,
    catalog_writable,
)
from avcad.workflow.architecture import select
from avcad.workflow.importers import (  # CSV 路径也要走同一套归一化（R12）
    build_entries, to_bom_csv, apply_category_fallback, read_xlsx_sheets)
from avcad.parse.product_resolver import enrich as resolve_products
from avcad.model.category_kb import usage_hint  # 设备类别知识库：第②步识别建议
from avcad.deliverables.ease_mapp import export_ease_package  # ② EASE/MAPP 对接导出
from scripts.check_overlap import check_svg

STATIC = os.path.join(os.path.dirname(__file__), "static")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# 进程内解析缓存（解析 + 主库补全结果），按 CSV 内容哈希
_ENTRY_CACHE: dict = {}

# ---------------- 产品主库（eko_catalog.json）读写 ----------------
# 与 catalog_resolver 共用同一份路径定义，避免出现第二个「主库副本」
from avcad.data.catalog_resolver import (  # noqa: E402
    DEFAULT_JSON as _CATALOG_PATH, CAT_SPEC, DRAW_EXCLUDE_BRANDS,
    safe_load_json,
)
_CATALOG = {"data": None, "mtime": 0}

# 音频知识库（static/audio_kb.json）进程内缓存，只读、不参与校验写盘
_KB_CACHE = {"data": None}

# 可编辑字段与取值提示（前端据此渲染控件）
CATEGORY_CHOICES = [
    "SOURCE", "WIRELESS_MIC", "WIRELESS_RX", "ANTENNA", "ANT_DIST", "ANT_COMBINE",
    "MIXER", "PROCESSOR", "SPEAKER_MGR", "AMP", "SPEAKER", "SWITCH",
    "IO", "MIC_HOST", "",
]
FEATURE_CHOICES = [
    "analog", "aes", "dante", "control", "wireless", "phantom",
    "mix_out", "trs_out", "dsp", "active",
]


def _load_catalog():
    """加载主库；文件 mtime 变化时自动重载，避免多进程写丢。"""
    try:
        mt = os.path.getmtime(_CATALOG_PATH)
    except OSError:
        return {"products": []}
    if _CATALOG["data"] is not None and _CATALOG["mtime"] == mt:
        return _CATALOG["data"]
    data = safe_load_json(_CATALOG_PATH)   # 损坏时透明回退 .bak（见 catalog_resolver）
    _CATALOG["data"] = data
    _CATALOG["mtime"] = mt
    return data


def _save_catalog():
    """原子写回主库：先备份再落盘，失败不破坏原文件。

    ★ 备份走 ``legend_sync.backup_catalog``：那边有 MAX_BACKUPS=5 的轮转。
      此前这里自己拼 .bak.时间戳且**无任何清理**，UI 上每改一条主库就
      多一份，avcad/data/ 会被上百份备份淹没。

    ★ 2026-09-01 R13：主库**不可写时抛 PermissionError**（而不是 OSError
      里淹没的 "[Errno 30] Read-only file system"）。打包版里主库是随包的
      只读内置基线，写它必然失败；这里是用户**主动**改主库，静默成功是骗人。
    """
    if not catalog_writable(_CATALOG_PATH):
        raise PermissionError(
            f"主库只读，无法保存（打包版主库是内置基线，请改图例库）：{_CATALOG_PATH}")
    data = _load_catalog()
    bak = backup_catalog(_CATALOG_PATH)
    tmp = f"{_CATALOG_PATH}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _CATALOG_PATH)
    _CATALOG["mtime"] = os.path.getmtime(_CATALOG_PATH)
    return bak


def _entries_from_bom(bom: str) -> list:
    """CSV / 文本清单 -> 规范化条目。

    ★ 2026-09-01 R12：``parse_bom`` 只做「读表」，不做任何归一化。此前
      xlsx 走 ``build_entries``（会 resolve 主库 + 类别兜底 IO + 抽特性参数），
      CSV / 文本走这里什么都不做 —— 同一个清单两种下场：
      文本 BOM 里「设备类型」列留空的未知型号，出图时 category 是空串，
      变成 **0 端口 + ERROR:UNKNOWN_TYPE**，而 xlsx 路径是 IO 1进1出。

      现在补齐两步，跟 ``build_entries`` 保持一致：
        1. ``resolve_products`` —— 主库 / 内置库补类别、特性、参数
        2. ``apply_category_fallback`` —— 认不出的兜底成 IO（后置型号排除）

      顺序不能反：enrich 只在 category 为空时才填，先兜底就把主库覆盖死了。
    """
    h = hashlib.sha1(bom.encode("utf-8")).hexdigest()
    if h in _ENTRY_CACHE:
        # ★ 缓存命中返回深拷贝：下游（run.py / _build_dxf_bytes）会原地改条目
        # （redundancy="NONE"、pop("pair")），直接返回共享列表会让下一次同 BOM
        # 请求拿到被污染的脏状态。深拷贝隔离，缓存原值保持干净。
        return copy.deepcopy(_ENTRY_CACHE[h])
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(bom)
        tmp = f.name
    try:
        entries = parse_bom(tmp)
    finally:
        os.unlink(tmp)
    resolve_products(entries)
    entries, _dropped = apply_category_fallback(entries)
    if len(_ENTRY_CACHE) < 128:
        _ENTRY_CACHE[h] = copy.deepcopy(entries)
    return entries


def _decode_upload(body: dict):
    """解析上传：xlsx(base64) 或 csv 文本 -> (entries, csv, dropped_names, notes, pages)。

    pages = [{name, csv, count}]：多工作表 xlsx 时每个工作表拆成一页（第⑤步按页出图、
    可一页一页导出）；单工作表 / csv / 文本清单则为单页。``csv`` 仍是全表聚合，供
    第②/③步模块与图例确认（型号级，跨页去重）使用。
    """
    pages = []
    if body.get("b64") and body.get("filename"):
        raw = base64.b64decode(body["b64"])
        ext = os.path.splitext(body["filename"])[1].lower()
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=ext) as f:
            f.write(raw)
            tmp = f.name
        try:
            if ext in (".xlsx", ".xls"):
                sheets = read_xlsx_sheets(tmp)
                if len(sheets) > 1:
                    # ★ 多页清单：每个工作表 = 一页（空表跳过，避免出幽灵页）
                    for name, _rows in sheets.items():
                        p_entries, _p_dropped = build_entries(tmp, sheet=name)
                        if not p_entries:
                            continue
                        pages.append({"name": name or "未命名页",
                                      "csv": to_bom_csv(p_entries),
                                      "count": len(p_entries)})
                    # 聚合全表供第②/③步（型号级跨页去重）
                    entries, dropped = build_entries(tmp)
                    csv = to_bom_csv(entries)
                else:
                    entries, dropped = build_entries(tmp)
                    csv = to_bom_csv(entries)
                    first = next(iter(sheets), "第1页")
                    pages.append({"name": first or "第1页", "csv": csv, "count": len(entries)})
            else:
                csv = open(tmp, encoding="utf-8-sig").read()
                entries = _entries_from_bom(csv)
                dropped = []
                pages.append({"name": "第1页", "csv": csv, "count": len(entries)})
        finally:
            os.unlink(tmp)
        note = f"已从 {body['filename']} 解析 {len(entries)} 个设备条目"
        if len(pages) > 1:
            note += f"（共 {len(pages)} 个工作表，已按页拆分）"
        notes = [note]
    else:
        csv = body.get("bom", "")
        entries = _entries_from_bom(csv)
        dropped = []
        pages.append({"name": "第1页", "csv": csv, "count": len(entries)})
        notes = [f"已从文本解析 {len(entries)} 个设备条目"]
    dropped_names = [d.get("设备名称") or d.get("名称") or "(未命名)" for d in dropped]
    if dropped_names:
        notes.append(f"已排除非信号设备 {len(dropped_names)} 项：{', '.join(dropped_names)}")
    return entries, csv, dropped_names, notes, pages


def _build_payload(entries, name="Web"):
    proj = build_project(entries, name=name)
    c = Canvas(); draw_devices(c, proj); draw_wires(c, proj)
    devices = [{
        "uid": i.uid, "name": i.name, "category": i.category, "model": i.model,
        "features": sorted(i.features), "params": i.params, "active": i.active,
        "redundancy": i.redundancy.value,
        "ports": [{"label": p.label, "signal": p.signal.value, "side": p.side} for p in i.ports],
    } for i in proj.instances]
    return {
        "svg": render_svg(c),
        "chain": proj.chain,
        "issues": [{"level": x.level, "code": x.code, "msg": x.msg} for x in proj.issues],
        "devices": devices,
        "name": proj.name,
    }


def _legend_usage(proj) -> dict:
    """统计工程中每个型号是否命中已确认图例（SWITCH 为自动生成的交换机，不计）。

    used    = 出图时严格复用了第③步确认的图例
    missing = 未确认，出图用的是引擎推断端口（属图例不一致）
    """
    st = LegendStore()
    used, missing, seen = [], [], set()
    for i in proj.instances:
        if i.category == "SWITCH":
            continue
        key = (i.brand or "", i.model or "")
        if key in seen:
            continue
        seen.add(key)
        item = {"brand": i.brand or "", "model": i.model or "", "name": i.name or "",
                "category": i.category, "ports": len(i.ports)}
        (used if st.has(i.brand or "", i.model or "", i.category or "")
         else missing).append(item)
    return {"used": used, "missing": missing,
            "total": len(used) + len(missing), "confirmed": len(used),
            "ok": not missing}


# ---------------- CAD 导出：目录选择 / 落盘 / 打开所在文件夹 ----------------
_INVALID_FNAME_CHARS = '<>:"/\\|?*'


def _safe_filename(name: str) -> str:
    """清理成安全文件名（保留中文），并确保 .dxf 后缀。"""
    n = (name or "").strip() or "AVCAD"
    n = "".join(ch for ch in n if ch not in _INVALID_FNAME_CHARS and ord(ch) >= 32)
    n = re.sub(r"\s+", "_", n).strip(" .")
    if len(n) > 120:
        n = n[:120]
    if not n:
        n = "AVCAD"
    if not n.lower().endswith(".dxf"):
        n += ".dxf"
    return n


def _build_project(data: dict):
    """按与页面预览完全一致的参数构建工程，返回 (proj, 工程名)。供 DXF 与 EASE 导出复用。"""
    bom = data.get("bom", "")
    anon = bool(data.get("anon", False))
    entries = _entries_from_bom(bom) if bom else []
    decisions = data.get("decisions") or None
    if decisions:
        entries, _ = confirm_modules(entries, decisions)
    lvl = data.get("redundancy", "NONE")
    if lvl in redundancy_levels():
        for e in entries:
            e["redundancy"] = "NONE"
            e.pop("pair", None)
        entries = _apply_redundancy(entries, lvl)
    # 与预览一致：同样应用第③步确认的图例
    proj = build_project(entries, name=data.get("name", "工作流系统"),
                         legend_store=LegendStore(), redundancy=lvl)
    return proj, anon


def _build_dxf_bytes(data: dict):
    """按与页面预览完全一致的参数构建 DXF，返回 (bytes, 工程名)。"""
    proj, anon = _build_project(data)
    c = Canvas()
    draw_devices(c, proj, anon=anon)
    draw_wires(c, proj, label_all=True)
    draw_ports(c, proj)
    draw_wire_legend(c, proj)        # 与预览一致：图幅底部线型说明
    out = tempfile.NamedTemporaryFile("wb", suffix=".dxf", delete=False)
    out.close()
    try:
        render_dxf(c, out.name, proj.name)
        with open(out.name, "rb") as fh:
            return fh.read(), proj.name
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


def _api_export_ease(data: dict):
    """导出 EASE/MAPP 对接包（speakers.csv / audience.csv / project.json / 可选 geometry.dxf）。

    坐标按 doc 35 Phase A 归一化（舞台中心 mm + scale）。geometry.dxf 由标准 DXF 导出复用，
    仅当 data["with_dxf"] 为真时附带。
    """
    dest = (data.get("dir") or "").strip()
    if not dest:
        return {"error": "未指定保存目录"}
    dest = os.path.abspath(os.path.expanduser(dest))
    if not os.path.isdir(dest):
        return {"error": f"目录不存在：{dest}"}
    if not os.access(dest, os.W_OK | os.X_OK):
        return {"error": f"没有写入权限：{dest}"}
    try:
        proj, _ = _build_project(data)
    except Exception as ex:
        return {"error": f"工程构建失败：{ex}"}
    dxf = None
    if data.get("with_dxf"):
        try:
            dxf, _ = _build_dxf_bytes(data)
        except Exception as ex:
            dxf = None  # DXF 失败不阻断 CSV/JSON 导出
    try:
        result = export_ease_package(proj, dest, dxf_bytes=dxf)
    except Exception as ex:
        return {"error": f"EASE 包导出失败：{ex}"}
    return {
        "dir": dest,
        "name": proj.name,
        "files": result["files"],
        "speaker_count": result["speaker_count"],
        "audience_count": result["audience_count"],
    }


def _pick_folder_native(prompt: str):
    """弹出系统原生「选择文件夹」对话框。

    返回文件夹路径；用户取消返回 None；系统不支持或调用失败抛 RuntimeError。
    """
    system = platform.system()
    if system == "Darwin":
        esc = (prompt or "选择保存位置").replace('"', "'")
        script = f'POSIX path of (choose folder with prompt "{esc}")'
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=600)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
        err = (p.stderr or "").strip()
        if "User canceled" in err or "-128" in err:
            return None
        raise RuntimeError(err or "系统文件夹窗口调用失败")
    if system == "Windows":
        desc = (prompt or "选择保存位置").replace('"', "'")
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
              f"$d.Description = \"{desc}\";"
              "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
              " { $d.SelectedPath }")
        p = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           capture_output=True, text=True, timeout=600)
        if p.returncode == 0:
            return p.stdout.strip() or None
        raise RuntimeError((p.stderr or "").strip() or "系统文件夹窗口调用失败")
    # Linux / 其它：优先 zenity，其次 tkinter
    if shutil.which("zenity"):
        p = subprocess.run(["zenity", "--file-selection", "--directory",
                            "--title", prompt or "选择保存位置"],
                           capture_output=True, text=True, timeout=600)
        return p.stdout.strip() or None
    code = ("import tkinter;"
            "from tkinter import filedialog;"
            "r=tkinter.Tk(); r.withdraw();"
            "print(filedialog.askdirectory(), end='')")
    p = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=600)
    if p.returncode == 0:
        return p.stdout.strip() or None
    raise RuntimeError("当前系统不支持原生文件夹选择窗口")


def _api_pick_folder(data: dict):
    try:
        picked = _pick_folder_native(data.get("prompt") or "选择 CAD 文件保存位置")
    except subprocess.TimeoutExpired:
        return {"error": "选择文件夹超时（窗口长时间未操作）"}
    except Exception as ex:
        return {"error": f"无法打开系统文件夹选择窗口：{ex}"}
    if not picked:
        return {"canceled": True}
    picked = os.path.abspath(os.path.expanduser(picked))
    if not os.path.isdir(picked):
        return {"error": f"所选路径不是有效文件夹：{picked}"}
    return {"path": picked}


def _api_export_save(data: dict):
    dest = (data.get("dir") or "").strip()
    if not dest:
        return {"error": "未指定保存目录"}
    dest = os.path.abspath(os.path.expanduser(dest))
    if not os.path.exists(dest):
        return {"error": f"目录不存在：{dest}"}
    if not os.path.isdir(dest):
        return {"error": f"不是文件夹：{dest}"}
    if not os.access(dest, os.W_OK | os.X_OK):
        return {"error": f"没有写入权限：{dest}（请换一个目录，或在系统中授予该目录的写权限）"}
    fname = _safe_filename(data.get("filename") or "AVCAD")
    full = os.path.join(dest, fname)
    try:
        dxf, proj_name = _build_dxf_bytes(data)
    except Exception as ex:
        return {"error": f"图纸生成失败：{ex}"}
    try:
        with open(full, "wb") as fh:
            fh.write(dxf)
    except PermissionError:
        return {"error": f"没有写入权限：{full}"}
    except OSError as ex:
        return {"error": f"写入失败：{ex}"}
    return {"path": full, "dir": dest, "filename": fname,
            "bytes": len(dxf), "name": proj_name}


def _api_export_all(data: dict):
    """一次性导出多页 DXF，打包成单 zip 流（内存 zipfile，不写盘）。

    入口契约（2026-09-04 阳哥报「快速下载只下一张」R_fix）：
      - 多页清单时，前端把每页 csv+name 放到 ``data["pages"]`` 列表
      - 缺省 / 单页退化为 ``data["bom"]``+``data["name"]``（与 ``/api/export`` 一致）
      - 每页用 ``_build_dxf_bytes`` 独立构建（保证与页面预览一致），
        文件名 ``<工程名>_<页名>.dxf``；页名空时退化为 ``page_<1-based>``
    返回：
      ``{"zip_b64": ..., "name": ..., "count": N, "bytes": total}``
    """
    pages = data.get("pages")
    name_base = data.get("name") or "AVCAD"
    if not pages:
        # 退化：单页 BOM，与 /api/export 同语义
        try:
            dxf, proj_name = _build_dxf_bytes(data)
        except Exception as ex:
            return {"error": f"图纸生成失败：{ex}"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_safe_filename(proj_name or name_base) + ".dxf", dxf)
        raw = buf.getvalue()
        return {"zip_b64": base64.b64encode(raw).decode("ascii"),
                "name": proj_name or name_base, "count": 1, "bytes": len(raw)}
    if not isinstance(pages, list):
        return {"error": "pages 必须是列表"}
    zbuf = io.BytesIO()
    used_names = set()
    try:
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, pg in enumerate(pages):
                if not isinstance(pg, dict):
                    return {"error": f"第 {i + 1} 页条目格式错（不是 dict）"}
                csv = pg.get("csv") or ""
                if not csv.strip():
                    return {"error": f"第 {i + 1} 页 CSV 为空"}
                pname = (pg.get("name") or "").strip() or f"page_{i + 1}"
                one = dict(data)            # 不污染调用方 data
                one["bom"] = csv
                one["name"] = pname
                try:
                    dxf, _ = _build_dxf_bytes(one)
                except Exception as ex:
                    return {"error": f"第 {i + 1} 页（{pname}）生成失败：{ex}"}
                base = _safe_filename(f"{name_base}_{pname}") or "AVCAD"
                if not base.lower().endswith(".dxf"):
                    dxf_name = base + ".dxf"
                else:
                    dxf_name = base
                # 防重名：同 base 加 _2 / _3 ...
                candidate = dxf_name
                k = 2
                while candidate in used_names:
                    stem, dot, ext = dxf_name.rpartition(".")
                    candidate = f"{stem}_{k}{dot}{ext}" if dot else f"{dxf_name}_{k}"
                    k += 1
                used_names.add(candidate)
                zf.writestr(candidate, dxf)
    except zipfile.BadZipFile as ex:
        return {"error": f"zip 写入失败：{ex}"}
    raw = zbuf.getvalue()
    if not raw:
        return {"error": "zip 为空"}
    return {"zip_b64": base64.b64encode(raw).decode("ascii"),
            "name": name_base, "count": len(pages), "bytes": len(raw)}


def _api_open_folder(data: dict):
    target = (data.get("path") or "").strip()
    if not target:
        return {"error": "未指定路径"}
    target = os.path.abspath(os.path.expanduser(target))
    if os.path.isfile(target):
        target = os.path.dirname(target)
    if not os.path.isdir(target):
        return {"error": f"目录不存在：{target}"}
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", target])
        elif system == "Windows":
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as ex:
        return {"error": f"无法打开文件夹：{ex}"}
    return {"ok": True, "path": target}


def _catalog_item(idx: int, p: dict) -> dict:
    """主库条目 -> 前端可编辑视图（只暴露需要人工校正的字段）。"""
    params = p.get("params") or {}
    # ★ R10 反向同步标记：从 params 里把 legend_rev / synced_at 抽到顶层
    # 前端主库卡顶部元信息行据此渲染「↘ 由图例 vN 反推」徽章
    return {
        "idx": idx,
        "brand": p.get("brand") or "",
        "model": p.get("model") or "",
        "name": p.get("name") or "",
        "section": p.get("section") or "",
        "category": p.get("category"),
        "defer_reason": p.get("defer_reason") or "",
        "features": list(p.get("features") or []),
        "params": params,
        "legend_rev": params.get("legend_rev"),     # R10：被图例库反推的 rev
        "synced_at": params.get("synced_at"),       # R10：反推时间戳
        "remark": p.get("remark") or "",
        "unit": p.get("unit") or "",
    }


def _module_item(m) -> dict:
    """模块条目 -> 前端视图。

    ★ ``source`` 是 R12 新增的收录状态（catalog/builtin/deferred/unknown）。
      unknown = 主库与内置库都没有这个型号，类别是名称关键词猜的、或兜底成 IO，
      出图后很可能是个孤立方块。第②步据此打「主库未收录」徽章，
      让阳哥一眼看到哪些型号需要先补图例。
    """
    return {"brand": m.brand, "model": m.model, "category": m.category,
            "name": m.name, "quantity": m.quantity, "decision": m.decision,
            "source": m.source,
            # 设备类别知识库识别建议：第②步在「主库未收录」徽章旁展示
            # 「这是什么 + 怎么接」，让阳哥一眼知道新型号该干什么
            "kb_hint": usage_hint(m.brand, m.model, m.name),
            # 特性与参数：第③步图例页要用它们让引擎展开端口初值
            "features": list(m.features), "params": dict(m.params)}


def _load_audio_kb():
    """读取 static/audio_kb.json 汇总的音频知识库（图例校正页右侧「资料查询」用）。

    纯只读，不触碰 eko_catalog.json / legend_library.json。文件缺失或解析失败
    时兜底返回空结构，前端不会崩。进程内缓存一次，避免每次打开抽屉都读盘。"""
    if _KB_CACHE["data"] is not None:
        return _KB_CACHE["data"]
    path = os.path.join(STATIC, "audio_kb.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            data = {"entries": [], "categories": [], "title": "音频知识库",
                    "version": ""}
    except Exception as ex:
        print(f"[AVCAD] 读取音频知识库失败: {ex}", file=sys.stderr)
        data = {"entries": [], "categories": [], "title": "音频知识库",
                "version": "", "error": str(ex)}
    _KB_CACHE["data"] = data
    return data


def _run_and_render(bom: str, anon: bool, decisions, redundancy, name: str):
    """跑一遍工作流并渲染单页 -> (page_dict, legend_dict)。

    单页与多页复用同一套绘制逻辑；多页时由 ``/api/run`` 逐页调用本函数。
    """
    result = run_workflow(
        entries=_entries_from_bom(bom) if bom else None,
        decisions=decisions, redundancy=redundancy, name=name)
    proj = result["project"]
    c = Canvas()
    draw_devices(c, proj, anon=anon)
    draw_wires(c, proj, label_all=True)
    draw_ports(c, proj)           # 端口在最上层，避免被连线覆盖
    draw_wire_legend(c, proj)     # 图幅底部线型说明
    arch = result["architecture"]
    legend = _legend_usage(proj)
    overlap, diagonal, ok = check_svg(render_svg(c))
    devices = [{
        "uid": i.uid, "name": i.name, "category": i.category,
        "brand": i.brand, "model": i.model,
        "features": sorted(i.features), "params": i.params, "active": i.active,
        "redundancy": i.redundancy.value,
        "ports": [{"label": p.label, "signal": p.signal.value, "side": p.side,
                   "role": p.role, "air": p.air} for p in i.ports],
    } for i in proj.instances]
    page = {
        "name": name,
        "csv": bom,        # 本页清单 CSV（单页 DXF 导出用）
        "svg": render_svg(c),
        "architecture": {"id": arch[0].id, "title": arch[0].title,
                         "score": round(arch[1], 1), "notes": arch[2]},
        "excluded": [{"brand": m.brand, "model": m.model, "name": m.name}
                     for m in result["excluded"]],
        "cache_miss": result["cache_miss"],
        "devices": devices,
        "legend": legend,
        "wireless": proj.meta.get("wireless_plan"),
        "wireless_warnings": proj.meta.get("wireless_warnings", []),
        "anon": anon,
        "summary": summarize(result),
        "issues": [{"level": i.level, "code": i.code, "msg": i.msg}
                   for i in proj.issues],
        "validation": {"overlap": overlap, "diagonal": diagonal, "ok": ok},
        "elapsed_ms": result.get("elapsed_ms"),
    }
    return page, legend


def _dispatch(path, body):
    if path == "/api/load-sample-xlsx":
        """载入桌面测试文件作为样例清单，前端「载入样例清单」按钮专用。"""
        sample_path = "/Users/mac/Desktop/测试.xlsx"
        if not os.path.exists(sample_path):
            return {"error": f"样例文件不存在：{sample_path}"}
        entries, dropped = build_entries(sample_path)
        csv = to_bom_csv(entries)
        dropped_names = [d.get("设备名称") or d.get("名称") or "(未命名)" for d in dropped]
        notes = [f"已从 测试.xlsx 解析 {len(entries)} 个设备条目"]
        if dropped_names:
            notes.append(f"已排除非信号设备 {len(dropped_names)} 项：{', '.join(dropped_names)}")
        return {"csv": csv, "dropped": dropped_names, "notes": notes}

    if path == "/api/parse":
        data = json.loads(body or "{}")
        entries, csv, dropped, notes, pages = _decode_upload(data)
        modules = build_module_list(entries)
        return {"csv": csv, "modules": [_module_item(m) for m in modules],
                "dropped": dropped, "notes": notes, "pages": pages,
                "unknown": [f"{m.brand} {m.model}".strip()
                            for m in modules if m.source == "unknown"]}

    if path == "/api/modules":
        data = json.loads(body or "{}")
        entries = _entries_from_bom(data.get("bom", ""))
        modules = build_module_list(entries)
        return {"modules": [_module_item(m) for m in modules],
                "unknown": [f"{m.brand} {m.model}".strip()
                            for m in modules if m.source == "unknown"]}

    if path == "/api/identify":
        """图例校正页「+ 手工添加型号」：输入品牌/型号即返回知识库识别建议
        （这是什么 + 怎么接 + 置信度），前端据此预填类别并提示人工确认。"""
        data = json.loads(body or "{}")
        return usage_hint(data.get("brand", "") or "", data.get("model", "") or "",
                          data.get("name", "") or "")

    if path == "/api/kb":
        """图例校正页右侧「资料查询」抽屉：返回汇总好的音频知识库
        （信号词汇 / 核心概念 / 校正常见坑 / 校验码速查），前端做模糊搜索。
        内容来自 static/audio_kb.json，纯只读、不参与校验写盘，安全。"""
        return _load_audio_kb()

    if path == "/api/legend-infer":
        """图例页端口初值：由**引擎规格模板**展开，取代前端硬编码模板。

        ★ R12：前端 ``defaultPorts(category)`` 的 switch 里没有 IO、也没有
        MIC_HOST 的 case，两者都落 default 分支 = XLR 4进4出。而引擎实际是
        IO 按 io.yaml 只有 1进1出（有 dante 特性才多一个 DANTE 口）、MIC_HOST
        按主库 ports_override 展开。同一个类别两套真相 = 用户在第③步看到的
        初值不是实际出图的样子，会照着错的数去改。

        只要类别有对应规格模板，一律以引擎为准；推断不出来才回落到前端模板。
        """
        data = json.loads(body or "{}")
        out, failed = {}, []
        for it in (data.get("items") or []):
            if not isinstance(it, dict):
                continue
            k = LegendStore.key(it.get("brand", ""), it.get("model", ""),
                                it.get("category", ""))
            lg = infer_from_entry(it)
            if lg is None:
                failed.append(k)
            else:
                out[k] = [p.to_dict() for p in lg.ports]
        return {"ports": out, "failed": failed}

    if path == "/api/legend":
        data = json.loads(body or "{}")
        st = LegendStore()
        if data.get("action") == "list":
            return {"legends": [lg.to_dict() for lg in st.all()],
                    "library": st.info()}
        if data.get("action") == "get":
            lg = st.get(data.get("brand", ""), data.get("model", ""),
                        data.get("category", ""))
            return {"legend": lg.to_dict() if lg else None, "library": st.info()}
        lg = Legend(
            brand=data.get("brand", ""), model=data.get("model", ""),
            category=data.get("category", ""),
            ports=[LegendPort(**p) for p in data.get("ports", [])],
            slots=data.get("slots", []), note=data.get("note", ""),
            electrical=data.get("electrical", {}) or {},
        )
        # 永久文档：确认即落盘（revision 递增 + 保留维护历史）
        st.put(lg, source="user")
        st.save()
        # ★ R10 反向同步：图例库 = 真相；落盘后立即把物理端口聚合数反推主库
        # 保留主库非端口字段（dsp/impedance_ohm/...）；标 legend_rev + synced_at
        rev_res = apply_reverse_to_catalog(lg)
        catalog_synced_item = None
        if rev_res.matched and isinstance(rev_res.product_index, int):
            # mtime 变了，下一次 _load_catalog 自动重读；这里直接读一次
            # 是为了把刚被反推的 params 打包给前端 banner 刷新徽章
            try:
                # ★ 必须从**刚写入的那份**主库回读，不能走 _load_catalog() 缓存：
                #   反推路径可被重定向（测试隔离 / AVCAD_CATALOG），缓存只认
                #   catalog_resolver.DEFAULT_JSON 这一份。走缓存会读到旧文件，
                #   前端拿到的 params 与真正落盘的不是同一份。
                with open(resolve_catalog_path(), encoding="utf-8") as _f:
                    cat = json.load(_f)
                prods = cat.get("products", [])
                if 0 <= rev_res.product_index < len(prods):
                    catalog_synced_item = _catalog_item(rev_res.product_index,
                                                        prods[rev_res.product_index])
            except Exception as _ex:
                print(f"[AVCAD] R10 反推主库后回读失败: {_ex}", file=sys.stderr)
        return {"ok": True, "revision": lg.revision,
                "updated_at": lg.updated_at, "library": st.info(),
                "catalog_synced": bool(rev_res.matched),
                "catalog_item": catalog_synced_item,
                "catalog_backup": (os.path.basename(rev_res.backup_path)
                                   if rev_res.backup_path else None)}

    if path == "/api/legend-catalog":
        """图例校正页数据源：**图例库** ∪ **主库中图例库尚未覆盖的产品**。

        语义（阳哥 2026-09-01）：
          - 这个页面只改**图例库**；主库的类别 / 参数 / 特性在此**只读展示**，
            用户没有必要动（要动也是脚本侧 build_catalog.py 的 MANUAL_* 表）。
          - 每条保存走 /api/legend，落图例库后由 R10 自动反推主库端口数。
          - 图例库没有的型号，端口初值由引擎按主库 params / ports_override
            推断（``infer_from_product``）；推断不出来就返回空端口让人工填。
        """
        data = json.loads(body or "{}")
        act = data.get("action") or "list"
        st = LegendStore()
        legends = st.all()
        by_key = {LegendStore.key(lg.brand, lg.model, lg.category): lg
                  for lg in legends}

        cat_data = _load_catalog()
        products = cat_data.get("products", [])
        brand_q = str(data.get("brand") or "").strip()
        kw = str(data.get("q") or "").strip().lower()
        only_missing = bool(data.get("only_missing"))
        try:
            limit = int(data.get("limit") or 300)
        except (TypeError, ValueError):
            limit = 300

        def _match(p, b, m):
            if brand_q and str(p.get("brand") or "").strip() != brand_q:
                return False
            if kw and kw not in f"{m} {p.get('name','')}".lower():
                return False
            return True

        items = []
        # ① 图例库已有的（人工确认过，排前面）
        for lg in legends:
            m = (lg.model or "").strip()
            if brand_q and (lg.brand or "").strip() != brand_q:
                continue
            if kw and kw not in f"{m} {lg.model}".lower():
                continue
            if only_missing:
                continue
            items.append({
                "key": LegendStore.key(lg.brand, lg.model, lg.category),
                "brand": lg.brand, "model": m, "category": lg.category,
                "name": "", "source": "legend",
                "revision": lg.revision, "updated_at": lg.updated_at,
                "ports": [p.to_dict() for p in lg.ports],
                "slots": list(lg.slots), "note": lg.note or "",
                "electrical": dict(lg.electrical or {}),
                "inferred": False,
            })
        # ② 主库里图例库尚未覆盖的
        for i, p in enumerate(products):
            if not isinstance(p, dict):
                continue
            b = (p.get("brand") or "").strip()
            m = (p.get("model") or "").strip()
            c = (p.get("category") or "").strip()
            if not b or not m:
                continue
            # 只列**可能出图**的型号：类别为空 / 无对应设备模板 / 品牌被排除
            # 的（配件、线缆、停产型号、Green-GO 等）列出来也不能建图例，
            # 只会把「未确认」列表淹掉（IPS 285 条里就有 200+ 条这类）。
            if c.upper() not in CAT_SPEC:
                continue
            if b.upper() in DRAW_EXCLUDE_BRANDS:
                continue
            if not _match(p, b, m):
                continue
            k = LegendStore.key(b, m, c)
            if k in by_key:
                continue
            if only_missing is False and len(items) >= limit:
                break
            lg = infer_from_product(p)
            items.append({
                "key": k, "brand": b, "model": m, "category": c,
                "name": p.get("name") or "", "source": "catalog",
                "revision": 0, "updated_at": "",
                "ports": [q.to_dict() for q in lg.ports] if lg else [],
                "slots": list(lg.slots) if lg else [], "note": "",
                "electrical": dict(lg.electrical or {}) if lg else {},
                "inferred": lg is not None,
                "catalog": _catalog_item(i, p),
            })
            if len(items) >= limit:
                break

        if act == "meta":
            counts = {}
            for p in products:
                if not isinstance(p, dict):
                    continue
                b = str(p.get("brand") or "").strip() or "(空)"
                counts[b] = counts.get(b, 0) + 1
            confirmed = {}
            for lg in legends:
                b = str(lg.brand or "").strip() or "(空)"
                confirmed[b] = confirmed.get(b, 0) + 1
            return {
                "total": len(products),
                "legend_count": len(legends),
                "library": st.info(),
                "path": str(_CATALOG_PATH),
                "brands": [{"brand": b, "count": c, "confirmed": confirmed.get(b, 0)}
                           for b, c in sorted(counts.items(),
                                              key=lambda x: (-x[1], x[0]))],
                "categories": CATEGORY_CHOICES,
            }
        return {"items": items, "total": len(items),
                "legend_count": len(legends), "library": st.info()}

    if path == "/api/architectures":
        data = json.loads(body or "{}")
        entries = _entries_from_bom(data.get("bom", ""))
        decisions = data.get("decisions") or None
        if decisions and not isinstance(decisions, dict):
            decisions = None
        if decisions:
            entries, _ = confirm_modules(entries, decisions)
        ranked = select(entries, data.get("redundancy"))
        return {"architectures": [
            {"id": t.id, "title": t.title, "desc": t.desc,
             "score": round(s, 1), "notes": n,
             "requires_redundancy": t.requires_redundancy}
            for (t, s, n) in ranked
        ]}

    if path == "/api/legend-check":
        """图例一致性检查：列出本工程中每个型号是否已确认图例。

        未确认的型号会在出图时被「引擎推断值」顶替，属图例不一致，
        前端应阻止出图并提示先回第③步确认。
        """
        data = json.loads(body or "{}")
        entries = _entries_from_bom(data.get("bom", ""))
        decisions = data.get("decisions") or None
        if decisions and not isinstance(decisions, dict):
            # 防御：decisions 异常/旧数据不是字典时忽略，避免确认函数内部出错
            decisions = None
        if decisions:
            entries, _ = confirm_modules(entries, decisions)
        st = LegendStore()
        seen, items = set(), []
        for e in entries:
            if not isinstance(e, dict):
                # 防御：跳过非字典条目，避免 'str' object has no attribute 'get'
                # 注：sys 已在模块顶层 import，这里不再 import —— 否则会把 _dispatch
                # 内的模块级 sys 遮蔽成局部变量，导致下方 614 行 except 日志触发
                # UnboundLocalError（既有潜伏 bug，2026-09-03 随写盘拦截暴露）
                print(f"[AVCAD] legend-check 跳过非字典条目: type={type(e).__name__} value={str(e)[:80]!r}",
                      file=sys.stderr)
                continue
            brand = (e.get("brand") or "").strip()
            model = (e.get("model") or "").strip()
            category = (e.get("category") or "").strip()
            if (brand, model, category) in seen:
                continue
            seen.add((brand, model, category))
            items.append({
                "brand": brand, "model": model, "category": category,
                "name": e.get("name") or "",
                "has_legend": st.has(brand, model, category),
            })
        missing = [x for x in items if not x["has_legend"]]
        return {"items": items, "missing": missing,
                "total": len(items), "confirmed": len(items) - len(missing),
                "ok": not missing}

    if path == "/api/catalog":
        """产品主库（eko_catalog.json）只读浏览 + 单条更正落盘。

        供「品牌校正页」逐条手动修正类别 / 特性 / 参数，修改即写回主库文件。
        """
        data = json.loads(body or "{}")
        act = data.get("action") or "list"
        cat = _load_catalog()
        products = cat.get("products", [])

        if act == "meta":
            counts = {}
            for p in products:
                b = str(p.get("brand") or "").strip() or "(空)"
                counts[b] = counts.get(b, 0) + 1
            return {
                "path": _CATALOG_PATH,
                "total": len(products),
                "brands": [{"brand": b, "count": c}
                           for b, c in sorted(counts.items(),
                                              key=lambda x: (-x[1], x[0]))],
                "categories": CATEGORY_CHOICES,
                "features": FEATURE_CHOICES,
            }

        if act == "put":
            idx = data.get("idx")
            if not isinstance(idx, int) or not (0 <= idx < len(products)):
                return {"error": f"idx 越界: {idx}"}
            prod = products[idx]
            if "category" in data:
                v = data["category"]
                prod["category"] = (str(v).strip() or None) if v is not None else None
            if "features" in data:
                prod["features"] = [str(x).strip().lower()
                                    for x in (data["features"] or []) if str(x).strip()]
            if "params" in data:
                prod["params"] = data["params"] or {}
            if "remark" in data:
                prod["remark"] = data["remark"] or ""
            bak = _save_catalog()   # 备份可能被轮转/失败 -> 允许为 None
            return {"ok": True,
                    "backup": os.path.basename(bak) if bak else None,
                    "item": _catalog_item(idx, products[idx])}

        # 默认 list：按品牌 + 关键字过滤
        brand = str(data.get("brand") or "").strip()
        kw = str(data.get("q") or "").strip().lower()
        only_wireless = bool(data.get("wireless_only"))
        items = []
        for i, p in enumerate(products):
            if brand and str(p.get("brand") or "").strip() != brand:
                continue
            if kw and kw not in f"{p.get('model','')} {p.get('name','')}".lower():
                continue
            if only_wireless and str(p.get("category")) not in (
                    "WIRELESS_RX", "WIRELESS_MIC", "ANTENNA", "ANT_DIST", "ANT_COMBINE"):
                continue
            items.append(_catalog_item(i, p))
        return {"items": items, "total": len(items)}


    if path == "/api/run":
        data = json.loads(body or "{}")
        anon = bool(data.get("anon", False))
        decisions = data.get("decisions") or None
        redundancy = data.get("redundancy")
        name = data.get("name", "工作流系统")
        # 多页清单：pages 为各工作表的 bom CSV 列表；缺省退化为单页（兼容旧前端）
        pages_in = data.get("pages") or [data.get("bom", "")]
        page_names = data.get("page_names") or {}
        t0 = time.perf_counter()
        # 图例闸门：聚合所有页统一校验一次（型号级，跨页去重），避免重复弹错
        if data.get("require_legend"):
            from avcad.core.build import build_project as _bp
            agg_entries = []
            for b in pages_in:
                if b:
                    agg_entries.extend(_entries_from_bom(b))
            agg_proj = _bp(agg_entries, name=name, legend_store=LegendStore(),
                           redundancy=redundancy)
            agg_legend = _legend_usage(agg_proj)
            if agg_legend["missing"]:
                return {"error": "图例未全部确认，已停止出图", "legend": agg_legend}
        pages_out = []
        for i, bom in enumerate(pages_in):
            pname = page_names.get(str(i)) or (
                f"第{i + 1}页" if len(pages_in) > 1 else name)
            page, _lg = _run_and_render(bom, anon, decisions, redundancy, pname)
            pages_out.append(page)
        # 顶层兼容旧前端：单页时把第 0 页字段平铺到顶层；多页时前端读 pages
        top = dict(pages_out[0]) if pages_out else {}
        top["pages"] = pages_out
        top["multi"] = len(pages_out) > 1
        if len(pages_out) > 1:
            top["summary"] = "\n".join(
                f"· {p['name']}：{p['summary'].splitlines()[0] if p.get('summary') else ''}"
                for p in pages_out)
            top["issues"] = [x for p in pages_out for x in p.get("issues", [])]
        top["build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return top

    if path == "/api/validate":
        data = json.loads(body or "{}")
        overlap, diagonal, ok = check_svg(data.get("svg", ""))
        return {"overlap": overlap, "diagonal": diagonal, "ok": ok}

    if path == "/api/export":
        dxf, name = _build_dxf_bytes(json.loads(body or "{}"))
        return {"dxf_b64": base64.b64encode(dxf).decode(), "name": name}

    if path == "/api/export-all":
        return _api_export_all(json.loads(body or "{}"))

    if path == "/api/export-save":
        return _api_export_save(json.loads(body or "{}"))

    if path == "/api/export-ease":
        return _api_export_ease(json.loads(body or "{}"))

    if path == "/api/pick-folder":
        return _api_pick_folder(json.loads(body or "{}"))

    if path == "/api/open-folder":
        return _api_open_folder(json.loads(body or "{}"))

    return {"error": "unknown endpoint"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, binary=False, ctype="application/json"):
        if binary:
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload); return
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html", "/catalog", "/catalog.html"):
            fname = "catalog.html" if "catalog" in p else "index.html"
            with open(os.path.join(STATIC, fname), encoding="utf-8") as f:
                raw = f.read().encode("utf-8")
            # 禁用缓存：改完前端后刷新即可拿到最新页面，避免「找不到新加的开关」
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif p == "/ping":
            self._send(200, {"ok": True, "service": "avcad-ui"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            self._send(200, _dispatch(urlparse(self.path).path, body))
        except Exception as ex:
            import traceback, sys
            print(f"[AVCAD] POST {self.path} 异常: {ex}\n{traceback.format_exc()}", file=sys.stderr)
            self._send(500, {"error": str(ex)})

    def log_message(self, *a):
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", "-p", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"AVCAD UI 已启动: {url}")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
