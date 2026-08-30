"""AVCAD macOS 应用启动器。

启动本地服务（与 `python -m avcad ui` 完全一致），自动打开浏览器，
并提供一个 Tk 控制面板显示访问地址、可重新打开浏览器 / 退出。

图例库（永久文档）写入用户目录，避免装在 /Applications 后只读：
    macOS : ~/Library/Application Support/AVCAD/legend_library.json
    Win   : %APPDATA%/AVCAD/legend_library.json
    Linux : ~/.local/share/avcad/legend_library.json
首次运行时把内置图例库复制过去，用户此后的维护都落在这里。
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

APP_TITLE = "AVCAD@Bailey@EZPRO"
# 自动化 / 排障用：
#   AVCAD_NO_BROWSER=1  不自动打开浏览器
#   AVCAD_NO_GUI=1      不显示 Tk 控制面板（纯命令行模式）
#   AVCAD_DEBUG_LOG=1   把启动诊断写到 <用户数据目录>/launch.log
NO_BROWSER = os.environ.get("AVCAD_NO_BROWSER") == "1"
NO_GUI = os.environ.get("AVCAD_NO_GUI") == "1"
DEBUG_LOG = os.environ.get("AVCAD_DEBUG_LOG") == "1"


def _enable_debug_log() -> None:
    """AVCAD_DEBUG_LOG=1 时把 stdout/stderr 重定向到 launch.log。

    windowed(.app) 模式下没有控制台，异常会静默消失；排障时靠这个日志文件。
    """
    if not DEBUG_LOG:
        return
    try:
        f = open(user_data_dir() / "launch.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


def dlog(msg: str) -> None:
    """启动诊断日志（仅在 AVCAD_DEBUG_LOG=1 时写入用户数据目录）。"""
    if not DEBUG_LOG:
        return
    try:
        with open(user_data_dir() / "launch.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def open_url(url: str) -> None:
    if NO_BROWSER:
        return
    webbrowser.open(url)


# ---------------- 数据与图例库路径 ----------------
def user_data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AVCAD"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "AVCAD"
    else:
        base = Path.home() / ".local" / "share" / "avcad"
    base.mkdir(parents=True, exist_ok=True)
    return base


def bundled_data_dir() -> Path | None:
    """PyInstaller 打包后数据目录位置。"""
    meipass = getattr(sys, "_MEIPASS", None)
    cands = []
    if meipass:
        cands.append(Path(meipass) / "avcad" / "data")
    here = Path(__file__).resolve().parent
    cands += [
        here / "avcad" / "data",
        here.parent / "avcad" / "data",
        here.parent / "avcad" / "avcad" / "data",
    ]
    for c in cands:
        if c.exists():
            return c
    return None


def prepare_legend_library() -> Path:
    dst = user_data_dir() / "legend_library.json"
    if not dst.exists():
        src = bundled_data_dir()
        if src and (src / "legend_library.json").exists():
            try:
                shutil.copy2(src / "legend_library.json", dst)
            except Exception:
                pass
    os.environ["AVCAD_LEGEND_LIBRARY"] = str(dst)
    return dst


def find_port(start: int = 8900, tries: int = 80) -> int:
    for p in range(start, start + tries):
        # 注意：不要设置 SO_REUSEADDR，否则在 macOS 上会「成功」绑到已被占用的 8900
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


# ---------------- 主流程 ----------------
def main() -> int:
    try:
        return _run()
    except Exception:
        dlog("启动异常:\n" + traceback.format_exc())
        raise


def _run() -> int:
    _enable_debug_log()
    lib_path = prepare_legend_library()
    dlog(f"图例库路径: {lib_path}  存在={lib_path.exists()}")
    dlog(f"sys.frozen={getattr(sys, 'frozen', False)} _MEIPASS={getattr(sys, '_MEIPASS', None)}")

    # 必须在导入 avcad 之前设置好图例库路径
    try:
        import avcad.ui.app as app  # noqa: E402
        from http.server import ThreadingHTTPServer  # noqa: E402
    except Exception as ex:  # 打包环境缺依赖时给出可见提示，而不是静默退出
        msg = f"启动失败：{ex}"
        print(msg, file=sys.stderr)
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            _r = _tk.Tk(); _r.withdraw()
            _mb.showerror(APP_TITLE, msg); _r.destroy()
        except Exception:
            pass
        return 1

    class _Server(ThreadingHTTPServer):
        """禁用端口复用（占用即报错），并跳过 server_bind 里的反向 DNS 查询。

        http.server 默认会执行 socket.getfqdn(host)，在部分网络环境下会卡几十秒，
        表现为「应用启动了但页面打不开」。这里直接用 IP 作为 server_name。
        """
        allow_reuse_address = False
        daemon_threads = True

        def server_bind(self):
            import socketserver
            socketserver.TCPServer.server_bind(self)
            host, port = self.server_address[:2]
            self.server_name = host
            self.server_port = port

    # 编码自检：utf-8-sig 缺失会导致上传 CSV / Excel 解析报 500
    try:
        import codecs
        codecs.lookup("utf-8-sig")
        dlog("codecs.lookup('utf-8-sig') OK")
    except Exception as ex:
        dlog(f"codecs.lookup('utf-8-sig') 失败: {ex}")

    port = find_port()
    dlog(f"选定端口: {port}")
    dlog("准备绑定端口 " + str(port))
    try:
        httpd = _Server(("127.0.0.1", port), app.Handler)
    except OSError as ex:
        msg = f"无法启动本地服务：{ex}"
        print(msg, file=sys.stderr)
        dlog(msg + "\n" + traceback.format_exc())
        return 1
    dlog("端口绑定成功")

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    dlog(f"服务已启动: {url}  (NO_GUI={NO_GUI})")

    # 无 GUI 环境（SSH / 纯命令行）时退化为命令行模式：服务已在后台线程运行
    def run_cli():
        print(f"AVCAD UI 已启动: {url}")
        dlog("进入命令行模式（无 Tk 面板）")
        open_url(url)
        try:
            threading.Event().wait()      # 服务在后台线程，保持进程存活
        except KeyboardInterrupt:
            pass
        dlog("命令行模式退出")

    if NO_GUI:
        run_cli()
        return 0

    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        run_cli()
        return 0

    try:
        root = tk.Tk()
    except Exception:
        run_cli()
        return 0
    root.title(APP_TITLE)
    root.geometry("560x260")
    root.resizable(False, False)

    bg = "#0b0f1a"
    root.configure(bg=bg)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground="#e9eef8")
    style.configure("Sub.TLabel", background=bg, foreground="#93a0b8")
    style.configure("Acc.TButton", padding=6)

    frm = ttk.Frame(root, padding=18)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text=APP_TITLE, font=("PingFang SC", 16, "bold")).pack(anchor="w")
    ttk.Label(frm, text="音视频系统图自动生成 · 已在浏览器中打开",
              style="Sub.TLabel").pack(anchor="w", pady=(2, 12))

    ttk.Label(frm, text="访问地址", style="Sub.TLabel").pack(anchor="w")
    url_var = tk.StringVar(value=url)
    url_entry = tk.Entry(frm, textvariable=url_var, readonlybackground="#141a2a",
                         foreground="#8ef0ff", relief="flat",
                         font=("SF Mono", 13), bd=0, highlightthickness=0)
    url_entry.pack(fill="x", pady=(2, 10))
    url_entry.configure(state="readonly")

    ttk.Label(frm, text=f"图例库（永久文档）：{lib_path}",
              style="Sub.TLabel", wraplength=520).pack(anchor="w", pady=(0, 14))

    btns = ttk.Frame(frm)
    btns.pack(fill="x")

    def open_browser():
        open_url(url)

    def copy_url():
        try:
            root.clipboard_clear()
            root.clipboard_append(url)
        except Exception:
            pass

    def quit_app():
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
        root.destroy()

    ttk.Button(btns, text="打开浏览器", command=open_browser).pack(side="left")
    ttk.Button(btns, text="复制地址", command=copy_url).pack(side="left", padx=8)
    ttk.Button(btns, text="退出并停止服务", command=quit_app).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.after(700, open_browser)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
