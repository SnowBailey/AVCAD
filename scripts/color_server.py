#!/usr/bin/env python3
"""线颜色配置本地服务器。

功能：
- 托管 deliverables/color_config 静态文件（颜色配置页）
- GET  /api/colors  -> 返回当前 avcad/config/signal_colors.json
- POST /api/colors  -> 接收浏览器提交的完整配色 JSON，写回文件
                       （下次 generate 自动生效；SVG 拓扑与 DXF 共用）

启动：python scripts/color_server.py
浏览器：http://localhost:8766/index.html
"""
from __future__ import annotations
import json
import os
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "deliverables" / "color_config"
CONFIG_FILE = ROOT / "avcad" / "config" / "signal_colors.json"
PORT = 8766

# 出厂默认配色（与 avcad/config/signal_colors.json 初始内容一致）。
# 备份 Dante 用紫色 #8a6bd4 以与模拟音频(XLR 青色)区分。
DEFAULT_COLORS = {
    "XLR":     {"label": "模拟音频 (XLR)",      "primary": {"color": "#5dcaa5", "layer": "WIRES_ANALOG",  "ltype": "solid"},   "backup": {"color": "#4fb38f", "layer": "WIRES_ANALOG",  "ltype": "dashed"}},
    "AES":     {"label": "数字音频 (AES3)",     "primary": {"color": "#3aa6a0", "layer": "WIRES_DIGITAL", "ltype": "solid"},   "backup": {"color": "#2f8c86", "layer": "WIRES_DIGITAL", "ltype": "dashed"}},
    "DANTE":   {"label": "网络音频 (Dante)",    "primary": {"color": "#378add", "layer": "WIRES_DANTE",   "ltype": "solid"},   "backup": {"color": "#8a6bd4", "layer": "WIRES_DANTE",   "ltype": "dashed"}},
    "RS232":   {"label": "控制 (RS-232)",       "primary": {"color": "#b07cd9", "layer": "WIRES_CONTROL", "ltype": "dotted"},  "backup": {"color": "#9a5fc4", "layer": "WIRES_CONTROL", "ltype": "dotted"}},
    "IP":      {"label": "控制/网络 (IP)",      "primary": {"color": "#b07cd9", "layer": "WIRES_CONTROL", "ltype": "dotted"},  "backup": {"color": "#9a5fc4", "layer": "WIRES_CONTROL", "ltype": "dotted"}},
    "GPIO":    {"label": "控制 (GPIO)",         "primary": {"color": "#b07cd9", "layer": "WIRES_CONTROL", "ltype": "dotted"},  "backup": {"color": "#9a5fc4", "layer": "WIRES_CONTROL", "ltype": "dotted"}},
    "RF":      {"label": "天线射频 (RF)",       "primary": {"color": "#e8923c", "layer": "WIRES_RF",      "ltype": "solid"},   "backup": {"color": "#cf7a2a", "layer": "WIRES_RF",      "ltype": "dashed"}},
    "SPEAKER": {"label": "扬声器线缆",          "primary": {"color": "#e8655a", "layer": "WIRES_SPEAKER", "ltype": "solid"},   "backup": {"color": "#cf5046", "layer": "WIRES_SPEAKER", "ltype": "dashed"}},
    "POWER":   {"label": "电源",                "primary": {"color": "#cfcfcf", "layer": "WIRES_POWER",   "ltype": "solid"},   "backup": {"color": "#b0b0b0", "layer": "WIRES_POWER",   "ltype": "dashed"}},
    "OPTICAL": {"label": "光纤",                "primary": {"color": "#7fd1e8", "layer": "WIRES_DIGITAL", "ltype": "solid"},   "backup": {"color": "#5fb8d0", "layer": "WIRES_DIGITAL", "ltype": "dashed"}},
}


def _load_cfg() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cfg(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_POST(self):
        if self.path != "/api/colors":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "invalid payload"})
                return
            # 基本校验：每个信号含 primary/backup 的 color
            for sig, entry in payload.items():
                if not isinstance(entry, dict):
                    self._send_json(400, {"error": f"bad entry for {sig}"})
                    return
                for role in ("primary", "backup"):
                    m = entry.get(role)
                    if not m or not isinstance(m, dict) or not m.get("color"):
                        self._send_json(400, {"error": f"{sig}.{role} 缺少 color"})
                        return
            _save_cfg(payload)
            self._send_json(200, {"ok": True, "path": str(CONFIG_FILE), "ts": int(time.time())})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_GET(self):
        if self.path == "/api/colors":
            self._send_json(200, _load_cfg())
            return
        if self.path == "/api/defaults":
            self._send_json(200, DEFAULT_COLORS)
            return
        return super().do_GET()


def main():
    STATIC.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _save_cfg(DEFAULT_COLORS)
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[color server] http://127.0.0.1:{PORT}/index.html")
    print(f"[config]       {CONFIG_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[color server] stopped")


if __name__ == "__main__":
    main()
