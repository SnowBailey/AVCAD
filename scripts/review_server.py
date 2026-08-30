#!/usr/bin/env python3
"""型号确认进度表本地服务器。

功能：
- 托管 deliverables/model_previews 静态文件
- 接收浏览器 POST /api/action：{brand, model, action, note?}
- 写入 review_state.json（按品牌/型号聚合的当前状态）
- 追加 pending_actions.jsonl（时间线日志，供 WorkBuddy 读取处理）

启动：python scripts/review_server.py
浏览器：http://localhost:8765/index.html
"""
from __future__ import annotations
import json
import os
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "deliverables" / "model_previews"
STATE_FILE = STATIC / "review_state.json"
PENDING_FILE = STATIC / "pending_actions.jsonl"
PORT = 8765


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _append_action(action: dict) -> None:
    line = json.dumps(action, ensure_ascii=False)
    with open(PENDING_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        # 简化日志，不污染终端
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
        if self.path != "/api/action":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}
            brand = payload.get("brand", "").strip()
            model = payload.get("model", "").strip()
            action = payload.get("action", "").strip()  # ok | modify | exclude
            note = payload.get("note", "").strip()
            if not brand or not model or action not in ("ok", "modify", "exclude"):
                self._send_json(400, {"error": "invalid params"})
                return

            state = _load_state()
            brand_state = state.setdefault(brand, {})
            brand_state[model] = {"status": action, "note": note, "ts": int(time.time())}
            _save_state(state)
            _append_action({
                "ts": time.time(),
                "brand": brand,
                "model": model,
                "action": action,
                "note": note,
            })
            self._send_json(200, {"ok": True, "action": action})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def do_GET(self):
        if self.path == "/api/state":
            self._send_json(200, _load_state())
            return
        if self.path == "/api/pending":
            actions = []
            if PENDING_FILE.exists():
                for line in PENDING_FILE.read_text(encoding="utf-8").strip().splitlines():
                    if line:
                        try:
                            actions.append(json.loads(line))
                        except Exception:
                            pass
            self._send_json(200, {"actions": actions})
            return
        return super().do_GET()


def main():
    STATIC.mkdir(parents=True, exist_ok=True)
    # 初始化空状态文件
    if not STATE_FILE.exists():
        _save_state({})
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[review server] http://127.0.0.1:{PORT}/index.html")
    print(f"[state]         {STATE_FILE}")
    print(f"[pending]       {PENDING_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review server] stopped")


if __name__ == "__main__":
    main()
