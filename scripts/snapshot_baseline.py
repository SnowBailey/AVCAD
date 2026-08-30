"""冻结已确认拓扑结果：拷贝 A-J 产物 + 计算 sha256 + 写 BASELINE_MANIFEST.json。

用法：python scripts/snapshot_baseline.py [--tag v1.0]
落盘后 deliverables/system_samples/BASELINE_<tag>/ 即为确认无误的可复现快照。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "deliverables", "system_samples")
SCENES = ["A_conference", "B_wireless", "C_foh", "D_distributed", "E_redundancy",
          "F_theatre", "G_studio", "H_pa", "I_touring", "J_multifunc"]

# 已确认（阳哥逐张验收通过）的渲染规则 v1.0 —— 冻结以保证可复现。
FROZEN_RULES = {
    "version": "1.0",
    "confirmed_at": None,  # 由调用时填充
    "rules": [
        "Dante 网络线最后生成（最上层）；底部总线式主线，设备 drop 从 stub 垂直落主线。",
        "主/备 Dante 主线按 role 全局岔开（一上一下），纵向 drop 水平偏移 12px 朝远离设备方向，不形成模块顶部 T 形出头。",
        "除交换机外，所有设备接口均为左/右结构出线；交换机 Dante 端口保留 top。",
        "IO 舞台接口箱/地插：IN 在 left，OUT + DANTE + AES + CTRL 在 right（全部水平出线）。",
        "所有非交换机设备的 CTRL 端口统一在 right（含 MIXER/PROCESSOR/WIRELESS_RX/SPEAKER_MGR/AMP/SPEAKER）。",
        "功放优先 8Ω 独立通道（每通道 1 只）；通道数不足时剩余扬声器并联到已有通道（并联连线画在同一通道口）。",
        "信号配色配置驱动 (config/signal_colors.json)：XLR 青、DANTE 蓝、备份 DANTE 紫虚线、AES 青、SPEAKER 红、RF 橙、控制紫；拓扑(SVG)与 CAD(DXF)共用。",
        "验收门禁：check_overlap 重叠=0、斜线=0；pytest 全通过。",
    ],
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1.0")
    args = ap.parse_args()

    dst = os.path.join(SRC, f"BASELINE_{args.tag}")
    os.makedirs(dst, exist_ok=True)

    manifest = {
        "tag": args.tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "AVCAD deterministic SVG/DXF generator",
        "frozen_rules": dict(FROZEN_RULES, confirmed_at=datetime.now(timezone.utc).isoformat()),
        "files": [],
    }

    copied = 0
    for s in SCENES:
        for ext in ("svg", "dxf"):
            src_file = os.path.join(SRC, f"sys_{s}.{ext}")
            if os.path.exists(src_file):
                shutil.copy2(src_file, os.path.join(dst, f"sys_{s}.{ext}"))
                manifest["files"].append({
                    "name": f"sys_{s}.{ext}", "sha256": _sha256(src_file), "size": os.path.getsize(src_file),
                })
                copied += 1
        # 同步 BOM 清单，便于复现
        bom = os.path.join(SRC, f"bom_{s}.csv")
        if os.path.exists(bom):
            shutil.copy2(bom, os.path.join(dst, f"bom_{s}.csv"))
            manifest["files"].append({"name": f"bom_{s}.csv", "sha256": _sha256(bom), "size": os.path.getsize(bom)})
    # gallery
    gal = os.path.join(SRC, "gallery.html")
    if os.path.exists(gal):
        shutil.copy2(gal, os.path.join(dst, "gallery.html"))
        manifest["files"].append({"name": "gallery.html", "sha256": _sha256(gal), "size": os.path.getsize(gal)})

    man_path = os.path.join(dst, "BASELINE_MANIFEST.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[ok] 基线快照 -> {dst}")
    print(f"     产物 {copied} 个 + manifest + gallery；SHA256 已写入 BASELINE_MANIFEST.json")
    print(f"     冻结规则版本 {manifest['frozen_rules']['version']}")


if __name__ == "__main__":
    main()
