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
import hashlib
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import ezdxf  # C 扩展在可用时自动启用，无需手动开启

from avcad.parse.bom_parser import parse_bom
from avcad.core.build import build_project, _apply_redundancy
from avcad.render.draw import draw_devices, draw_wires, draw_ports, draw_wire_legend
from avcad.render.primitives import Canvas
from avcad.render.svg_render import render_svg
from avcad.render.dxf_render import render_dxf
from avcad.workflow.module_confirm import build_module_list, confirm_modules
from avcad.workflow.run import run_workflow, summarize
from avcad.workflow.legend_store import LegendStore, Legend, LegendPort
from avcad.workflow.architecture import select
from avcad.workflow.importers import build_entries, to_bom_csv
from scripts.check_overlap import check_svg

STATIC = os.path.join(os.path.dirname(__file__), "static")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# 进程内解析缓存（解析 + 主库补全结果），按 CSV 内容哈希
_ENTRY_CACHE: dict = {}


def _entries_from_bom(bom: str) -> list:
    h = hashlib.sha1(bom.encode("utf-8")).hexdigest()
    if h in _ENTRY_CACHE:
        return _ENTRY_CACHE[h]
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(bom)
        tmp = f.name
    try:
        entries = parse_bom(tmp)
    finally:
        os.unlink(tmp)
    if len(_ENTRY_CACHE) < 128:
        _ENTRY_CACHE[h] = entries
    return entries


def _decode_upload(body: dict):
    """解析上传：xlsx(base64) 或 csv 文本 -> (entries, csv, dropped_names, notes)。"""
    if body.get("b64") and body.get("filename"):
        raw = base64.b64decode(body["b64"])
        ext = os.path.splitext(body["filename"])[1].lower()
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=ext) as f:
            f.write(raw)
            tmp = f.name
        try:
            if ext in (".xlsx", ".xls"):
                entries, dropped = build_entries(tmp)
                csv = to_bom_csv(entries)
            else:
                csv = open(tmp, encoding="utf-8-sig").read()
                entries = _entries_from_bom(csv)
                dropped = []
        finally:
            os.unlink(tmp)
        notes = [f"已从 {body['filename']} 解析 {len(entries)} 个设备条目"]
    else:
        csv = body.get("bom", "")
        entries = _entries_from_bom(csv)
        dropped = []
        notes = [f"已从文本解析 {len(entries)} 个设备条目"]
    dropped_names = [d.get("设备名称") or d.get("名称") or "(未命名)" for d in dropped]
    if dropped_names:
        notes.append(f"已排除非信号设备 {len(dropped_names)} 项：{', '.join(dropped_names)}")
    return entries, csv, dropped_names, notes


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


def _build_dxf_bytes(data: dict):
    """按与页面预览完全一致的参数构建 DXF，返回 (bytes, 工程名)。"""
    bom = data.get("bom", "")
    anon = bool(data.get("anon", False))
    entries = _entries_from_bom(bom) if bom else []
    decisions = data.get("decisions") or None
    if decisions:
        entries, _ = confirm_modules(entries, decisions)
    lvl = data.get("redundancy", "NONE")
    if lvl in ("PROCESSOR_BACKUP", "LINK_BACKUP", "FULL_CHAIN"):
        for e in entries:
            e["redundancy"] = "NONE"
            e.pop("pair", None)
        entries = _apply_redundancy(entries, {"MIXER": lvl})
        if lvl == "FULL_CHAIN":
            entries = _apply_redundancy(entries, {"PROCESSOR": lvl})
    # 与预览一致：同样应用第③步确认的图例
    proj = build_project(entries, name=data.get("name", "工作流系统"),
                         legend_store=LegendStore())
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


def _dispatch(path, body):
    if path == "/api/parse":
        data = json.loads(body or "{}")
        entries, csv, dropped, notes = _decode_upload(data)
        modules = build_module_list(entries)
        return {"csv": csv, "modules": [
            {"brand": m.brand, "model": m.model, "category": m.category,
             "name": m.name, "quantity": m.quantity, "decision": m.decision}
            for m in modules
        ], "dropped": dropped, "notes": notes}

    if path == "/api/modules":
        data = json.loads(body or "{}")
        entries = _entries_from_bom(data.get("bom", ""))
        modules = build_module_list(entries)
        return {"modules": [
            {"brand": m.brand, "model": m.model, "category": m.category,
             "name": m.name, "quantity": m.quantity, "decision": m.decision}
            for m in modules
        ]}

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
        )
        # 永久文档：确认即落盘（revision 递增 + 保留维护历史）
        st.put(lg, source="user")
        st.save()
        return {"ok": True, "revision": lg.revision,
                "updated_at": lg.updated_at, "library": st.info()}

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
                import sys
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

    if path == "/api/run":
        data = json.loads(body or "{}")
        bom = data.get("bom", "")
        anon = bool(data.get("anon", False))
        t0 = time.perf_counter()
        result = run_workflow(
            bom_text=bom, entries=_entries_from_bom(bom) if bom else None,
            decisions=data.get("decisions") or None,
            redundancy=data.get("redundancy"), name=data.get("name", "工作流系统"),
        )
        proj = result["project"]
        c = Canvas()
        draw_devices(c, proj, anon=anon)
        draw_wires(c, proj, label_all=True)
        draw_ports(c, proj)   # 端口在最上层，避免被连线覆盖
        draw_wire_legend(c, proj)   # 图幅底部线型说明
        arch = result["architecture"]
        legend = _legend_usage(proj)
        if data.get("require_legend") and legend["missing"]:
            return {"error": "图例未全部确认，已停止出图", "legend": legend}
        overlap, diagonal, ok = check_svg(render_svg(c))
        devices = [{
            "uid": i.uid, "name": i.name, "category": i.category,
            "brand": i.brand, "model": i.model,
            "features": sorted(i.features), "params": i.params, "active": i.active,
            "redundancy": i.redundancy.value,
            "ports": [{"label": p.label, "signal": p.signal.value, "side": p.side,
                       "role": p.role, "air": p.air} for p in i.ports],
        } for i in proj.instances]
        return {
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
            "build_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    if path == "/api/validate":
        data = json.loads(body or "{}")
        overlap, diagonal, ok = check_svg(data.get("svg", ""))
        return {"overlap": overlap, "diagonal": diagonal, "ok": ok}

    if path == "/api/export":
        dxf, name = _build_dxf_bytes(json.loads(body or "{}"))
        return {"dxf_b64": base64.b64encode(dxf).decode(), "name": name}

    if path == "/api/export-save":
        return _api_export_save(json.loads(body or "{}"))

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
        if p in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
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
