"""对打包后的 .app 启动的服务做功能冒烟测试。

覆盖：首页 / ping / 解析 / 图例库 / 架构 / 出图（含 check_overlap、线型说明）/ 导出 DXF。
用法: AVCAD_BASE=http://127.0.0.1:8901 python packaging/smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("AVCAD_BASE", "http://127.0.0.1:8901")

BOM = "\n".join([
    "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源",
    "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,",
    "PROCESSOR,,Xilica,音频处理器,1,dante,inputs=8;outputs=8,,",
    "SOURCE,,,,会议话筒,4,,,",
    "WIRELESS_MIC,Shure,QLXD1,无线话筒发射端,2,,,",
    "WIRELESS_RX,Shure,QLXD4,无线接收机,2,dante,channels=2,,",
    "ANT_DIST,Shure,UA844,天线分配器,1,",
    "ANTENNA,Shure,UA874,有源指向天线,2,,,",
    "AMP,Powersoft,Q,功放,2,,channels=2,,",
    "SPEAKER,L-Acoustics,KARA,主扩扬声器,4,,impedance_ohm=8;power_w=400,,",
    "NETWORK,Cisco,SG350,控制交换机,1,,ports=24,,",
])

fails = 0


def ok(cond, msg):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fails += 1


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:800]
        except Exception:
            pass
        return {"error": f"HTTP {e.code}", "detail": detail}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.read()


def main():
    print(f"目标：{BASE}")

    print("【1】静态页面")
    html = get("/").decode("utf-8")
    ok("AVCAD@Bailey@EZPRO" in html, "首页包含标题 AVCAD@Bailey@EZPRO")
    ok("intro-overlay" in html, "首页包含开场动画层")
    ok(len(html) > 20000, f"首页字节数 {len(html)}")

    print("【2】解析清单")
    res = post("/api/parse", {"bom": BOM})
    ok(not res.get("error"), "解析无错误")
    ok(len(res.get("modules", [])) >= 8, f"模块数 {len(res.get('modules', []))}")

    print("【3】图例库（内置数据是否随包带上）")
    lib = post("/api/legend", {"action": "list"})
    ok(len(lib.get("legends", [])) >= 20,
       f"内置图例库条数 {len(lib.get('legends', []))}（说明 avcad/data 已打包）")

    print("【4】参考架构")
    arch = post("/api/architectures", {"bom": BOM, "redundancy": "NONE"})
    ok(len(arch.get("architectures", [])) > 0,
       f"架构候选 {len(arch.get('architectures', []))} 个")

    print("【5】出图（含 check_overlap 与线型说明）")
    run = post("/api/run", {"bom": BOM, "redundancy": "NONE",
                            "name": "打包冒烟", "anon": False})
    ok(not run.get("error"), "出图无错误：" + str(run.get("error", "")) +
       (" " + str(run.get("detail", "")) if run.get("detail") else ""))
    svg = run.get("svg", "")
    ok(len(svg) > 10000, f"SVG 长度 {len(svg)}")
    ok("线型说明" in svg, "SVG 含线型说明表")
    v = run.get("validation", {})
    ok(v.get("ok"), f"校验 overlap={v.get('overlap')} diagonal={v.get('diagonal')}")

    print("【6】导出 DXF（ezdxf 在包内可用）")
    exp = post("/api/export", {"bom": BOM, "redundancy": "NONE", "name": "打包冒烟"})
    ok(not exp.get("error"), "导出无错误：" + str(exp.get("error", "")) +
       (" " + str(exp.get("detail", "")) if exp.get("detail") else ""))
    ok("dxf_b64" in exp and len(exp["dxf_b64"]) > 50000,
       f"DXF base64 长度 {len(exp.get('dxf_b64', ''))}")

    print("\nRESULT: " + ("FAIL ❌ (%d)" % fails if fails else "PASS ✅"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
