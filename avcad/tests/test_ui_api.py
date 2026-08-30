"""交互页面后端 API 测试（覆盖完整 5 步工作流端点）。"""
import base64
import json
import os

import pytest

import avcad.ui.app as app
from avcad.workflow.importers import build_entries, to_bom_csv

SAMPLE = (
    "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
    "SOURCE,,,,会议话筒,4,,,,\n"
    "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n"
    "PROCESSOR,BSS,BLU-806,音频处理器,1,dante;control,inputs=8;outputs=8,,system,\n"
    "AMP,Powersoft,Q,功放,1,dante;control;analog,channels=4,,,\n"
    "SPEAKER,L-Acoustics,KARA,主扩,4,,impedance_ohm=8;power_w=400,,,\n"
)


def _call(path, body):
    return app._dispatch(path, json.dumps(body or {}))


def test_parse_csv():
    r = _call("/api/parse", {"bom": SAMPLE})
    assert len(r["modules"]) == 5
    assert r["csv"]
    assert any("解析" in n for n in r["notes"])


def test_architectures_ranked():
    p = _call("/api/parse", {"bom": SAMPLE})
    a = _call("/api/architectures", {"bom": p["csv"]})
    assert a["architectures"]
    scores = [x["score"] for x in a["architectures"]]
    assert scores == sorted(scores, reverse=True)


def test_run_validation_ok():
    p = _call("/api/parse", {"bom": SAMPLE})
    r = _call("/api/run", {"bom": p["csv"], "redundancy": "PROCESSOR_BACKUP", "name": "T"})
    assert r["svg"]
    assert r["validation"]["ok"] is True
    assert r["validation"]["overlap"] == 0
    assert r["validation"]["diagonal"] == 0
    assert "elapsed_ms" in r


def test_export_dxf():
    p = _call("/api/parse", {"bom": SAMPLE})
    e = _call("/api/export", {"bom": p["csv"], "redundancy": "PROCESSOR_BACKUP", "name": "T"})
    assert len(e["dxf_b64"]) > 1000


def test_parse_xlsx_real(tmp_path):
    # 用真实清单生成 xlsx 后回读，验证上传路径
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["设备名称", "品牌", "型号", "数量", "指标参数"])
    ws.append(["会议话筒", "", "", 4, ""])
    ws.append(["数字调音台", "Yamaha", "TF5", 1, "dante;control"])
    ws.append(["线阵列扬声器吊架", "", "", 2, ""])  # 应被排除
    path = tmp_path / "t.xlsx"
    wb.save(path)
    b64 = base64.b64encode(path.read_bytes()).decode()
    r = _call("/api/parse", {"filename": "t.xlsx", "b64": b64})
    assert len(r["modules"]) >= 2
    assert any("吊架" in d for d in r["dropped"])


def test_legend_put_get(monkeypatch, tmp_path):
    # 用临时缓存文件，避免测试污染真实的 avcad/data/legend_cache.json
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "legend_test.json")
    k = _call("/api/legend", {"action": "put", "brand": "Yamaha", "model": "TF5",
                              "category": "MIXER",
                              "ports": [{"signal": "DANTE", "role": "io", "side": "left", "count": 2, "label": "DANTE"}]})
    assert k["ok"]
    g = _call("/api/legend", {"action": "get", "brand": "Yamaha", "model": "TF5"})
    assert g["legend"]["ports"][0]["signal"] == "DANTE"


def test_legend_check_reports_missing_then_ok(monkeypatch, tmp_path):
    """未确认图例 → ok=False；确认后 → ok=True。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "lc.json")
    bom = ("设备类型,品牌,型号,名称,数量,特性,参数\n"
           "MIXER,ZZB,ZZM,测试台,1,,\n")
    r = _call("/api/legend-check", {"bom": bom})
    assert r["total"] == 1 and r["confirmed"] == 0 and r["ok"] is False
    assert r["missing"][0]["model"] == "ZZM"

    _call("/api/legend", {"action": "put", "brand": "ZZB", "model": "ZZM",
                          "category": "MIXER",
                          "ports": [{"signal": "XLR", "role": "in", "side": "left",
                                     "count": 2, "label": "IN"}]})
    r2 = _call("/api/legend-check", {"bom": bom})
    assert r2["ok"] is True and r2["confirmed"] == 1 and not r2["missing"]


def test_run_anon_hides_brand_and_model(monkeypatch, tmp_path):
    """anon=True 时 SVG 不出现厂商/型号，改为类别代号；线标始终存在。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "lanon.json")
    _call("/api/legend", {"action": "put", "brand": "ZZB", "model": "ZZM",
                          "category": "MIXER",
                          "ports": [{"signal": "XLR", "role": "in", "side": "left",
                                     "count": 2, "label": "IN"}]})
    bom = ("设备类型,品牌,型号,名称,数量,特性,参数\n"
           "MIXER,ZZB,ZZM,测试台,1,,\n")
    full = _call("/api/run", {"bom": bom})
    anon = _call("/api/run", {"bom": bom, "anon": True})
    assert "ZZB" in full["svg"] and "ZZM" in full["svg"]
    assert "ZZB" not in anon["svg"]
    assert "ZZM" not in anon["svg"]
    assert "MIX-01" in anon["svg"]           # 类别代号


def test_wire_labels_present(monkeypatch, tmp_path):
    """有连线的工程，每条连线都应带 data-layer=WIRE_LABELS 的线标。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "lwire.json")
    r = _call("/api/run", {"bom": SAMPLE})   # 完整链路：话筒→调音台→处理器→功放→音箱
    svg = r["svg"]
    n_wires = svg.count("<polyline")
    n_labels = svg.count('data-layer="WIRE_LABELS"')
    assert n_wires > 0
    assert n_labels == n_wires, f"连线 {n_wires} 条但线标只有 {n_labels} 个"


def test_run_require_legend_blocks_missing(monkeypatch, tmp_path):
    """require_legend=True 且图例未确认 → 拒绝出图。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "lreq.json")
    bom = ("设备类型,品牌,型号,名称,数量,特性,参数\n"
           "MIXER,NOPE,NOPE2,台子,1,,\n")
    r = _call("/api/run", {"bom": bom, "require_legend": True})
    assert "error" in r and "图例未全部确认" in r["error"]
    assert r["legend"]["missing"]


def test_excluded_module_not_in_export():
    bom = (
        "设备类型,品牌,型号,名称,数量,特性,参数\n"
        "SOURCE,,,,麦克风,2,,,,\n"
        "SPEAKER,,箱子,1,,,\n"
    )
    # 排除 SPEAKER
    r = _call("/api/run", {"bom": bom, "decisions": {"箱子": "exclude"}})
    models = {m["model"] for m in r["excluded"]}
    assert "箱子" in models


def test_legend_check_excludes_module_by_decision():
    """第二步点「不需要」的型号不应出现在图例一致性检查中。"""
    bom = (
        "设备类型,品牌,型号,名称,数量,特性,参数\n"
        "MIXER,Yamaha,TF5,调音台,1,,\n"
        "SPEAKER,L-Acoustics,KARA,音箱,1,,\n"
    )
    r = _call("/api/legend-check", {"bom": bom, "decisions": {"Yamaha::TF5": "exclude"}})
    models = {m["model"] for m in r["items"]}
    assert "TF5" not in models
    assert "KARA" in models
    assert r["total"] == 1


def test_architectures_excludes_module_by_decision():
    """第二步点「不需要」的型号不应影响参考架构推荐。"""
    bom = (
        "设备类型,品牌,型号,名称,数量,特性,参数\n"
        "MIXER,Yamaha,TF5,调音台,1,,\n"
        "SPEAKER,L-Acoustics,KARA,音箱,1,,\n"
    )
    r = _call("/api/architectures", {"bom": bom, "decisions": {"Yamaha::TF5": "exclude"}})
    assert r["architectures"]


# ---------- 线标落位：统一贴在「模块右出线」上方 ----------
FULL_SAMPLE = (
    "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
    "SOURCE,,,,会议话筒,4,,,,\n"
    "WIRELESS_MIC,,,,无线话筒,2,,,,\n"
    "WIRELESS_RX,Shure,ULXD4D,无线接收机,1,dante;control,channels=2,,,\n"
    "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n"
    "PROCESSOR,BSS,BLU-806,音频处理器,1,dante;control,inputs=8;outputs=8,,system,\n"
    "AMP,Powersoft,Quattrocanali 4804,功放,1,dante;control;analog,channels=4,,,\n"
    "SPEAKER,L-Acoustics,KARA,主扩,4,,impedance_ohm=8;power_w=400,,,\n"
    "SWITCH,Cisco,CBS350-8T,Dante 交换机,1,dante,,,,\n"
)


def _svg_labels_above_exit(svg_text: str):
    """返回 (总线标数, 落在水平出线段上方 1~6px 内的线标数)。"""
    import re
    import xml.etree.ElementTree as ET
    NS = "{http://www.w3.org/2000/svg}"
    root = ET.fromstring(svg_text)
    segs_h = []
    for el in root.iter(NS + "polyline"):
        pts = [(float(a), float(b))
               for a, b in re.findall(r"(-?[\d.]+),(-?[\d.]+)", el.get("points", ""))]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(y2 - y1) < 0.5 and abs(x2 - x1) >= 0.5:
                segs_h.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
    total = above = 0
    for el in root.iter(NS + "text"):
        if el.get("data-layer") != "WIRE_LABELS":
            continue
        total += 1
        x = float(el.get("x", 0)); y = float(el.get("y", 0))
        size = float(el.get("font-size", 7) or 7)
        w = len(el.text or "") * size * 0.60
        anchor = el.get("text-anchor", "start")
        x0 = x if anchor == "start" else (x - w if anchor == "end" else x - w / 2)
        x1 = x0 + w
        for sx0, sx1, sy in segs_h:
            if x1 < sx0 - 2 or x0 > sx1 + 2:
                continue
            if 1.0 <= (sy - y) <= 6.0:
                above += 1
                break
    return total, above


def test_wire_labels_above_right_exit():
    """所有线标应贴在连线（优先模块右出线段）的正上方。"""
    svg = _call("/api/run", {"bom": FULL_SAMPLE})["svg"]
    total, above = _svg_labels_above_exit(svg)
    assert total > 0, "样例应产生连线线标"
    assert above / total >= 0.95, f"仅 {above}/{total} 条线标位于出线上方"


def test_pick_label_pos_prefers_earliest_horizontal():
    """_pick_label_pos 优先选靠源端的水平段，落点在线上方 3px（贴线）、左对齐向右展开。"""
    from avcad.render.draw import _pick_label_pos
    # 路径：贴模块右侧短 stub(y=50) → 长水平出线(y=50) → 垂直 → 目标 stub(y=120)
    pts = [(100, 50), (110, 50), (190, 50), (190, 120), (210, 120)]
    pos = _pick_label_pos("XLR", pts, [], [], size=7)
    assert pos is not None
    x, y, anchor, _box = pos
    assert abs(y - 47.0) < 0.01, f"线标应贴水平出线上方 3px，实际 y={y}"
    assert anchor == "start", f"应左对齐沿出线方向展开，实际 anchor={anchor}"
    # 落点应位于某条水平段的起点侧（stub 起点 100 或长段起点 110）
    assert abs(x - 100) < 0.01 or abs(x - 110) < 0.01, f"应从出线段起点开始，实际 x={x}"


def test_pick_label_pos_avoids_module_rect():
    """出线端被模块矩形挡住时，应沿出线方向后移，而不是掉到线下方。"""
    from avcad.render.draw import _pick_label_pos
    pts = [(100, 50), (110, 50), (190, 50), (190, 120), (210, 120)]
    rects = [(20, 20, 90, 80)]      # x 20..110 —— 挡住出线起点
    pos = _pick_label_pos("XLR", pts, rects, [], size=7)
    assert pos is not None
    x, y, anchor, _box = pos
    assert y < 50, f"仍应在出线上方，实际 y={y}"
    assert x >= 110, f"应避开模块矩形向右让开，实际 x={x}"


# ---------- 导出 CAD：选目录 / 落盘 / 打开所在文件夹 ----------
def test_safe_filename_strips_and_appends_dxf():
    from avcad.ui.app import _safe_filename
    assert _safe_filename("AVCAD 系统图") == "AVCAD_系统图.dxf"
    assert _safe_filename("a/b:c*?.txt") == "abc.txt.dxf"
    assert _safe_filename("") == "AVCAD.dxf"
    assert _safe_filename("已经.dxf") == "已经.dxf"


def test_export_save_writes_dxf(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    r = _call("/api/export-save", {"bom": SAMPLE, "dir": str(d),
                                   "filename": "我的系统图"})
    assert "error" not in r, r
    assert r["filename"] == "我的系统图.dxf"
    assert os.path.getsize(r["path"]) > 1000, "DXF 不应为空"
    with open(r["path"], "rb") as fh:
        assert fh.read(4) != b"", "文件可读"


def test_export_save_missing_dir():
    r = _call("/api/export-save", {"bom": SAMPLE, "dir": "/no/such/dir/xyz"})
    assert "error" in r and "目录不存在" in r["error"]


def test_export_save_no_permission(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    os.chmod(d, 0o500)          # 只读
    try:
        r = _call("/api/export-save", {"bom": SAMPLE, "dir": str(d)})
        assert "error" in r, "只读目录应报错"
        assert "没有写入权限" in r["error"], r
    finally:
        os.chmod(d, 0o700)


def test_export_save_requires_dir():
    r = _call("/api/export-save", {"bom": SAMPLE})
    assert "error" in r and "未指定保存目录" in r["error"]


def test_open_folder_rejects_bad_path():
    assert "error" in _call("/api/open-folder", {})
    assert "error" in _call("/api/open-folder", {"path": "/no/such/dir/xyz"})


def test_pick_folder_is_reachable():
    """/api/pick-folder 端点已注册（不真正弹窗，故不在此调用）。"""
    src = open(os.path.join(os.path.dirname(app.__file__), "app.py"), encoding="utf-8").read()
    assert '"/api/pick-folder"' in src and '"/api/open-folder"' in src


# ---------- 线型说明（图例表） ----------
LEG_BOM = (
    "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
    "SOURCE,,,,会议话筒,4,,,,\n"
    "WIRELESS_MIC,,,,无线话筒,2,,,,\n"
    "WIRELESS_RX,Shure,ULXD4D,无线接收机,1,dante;control,channels=2,,,\n"
    "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n"
    "PROCESSOR,BSS,BLU-806,音频处理器,1,dante;control,inputs=8;outputs=8,,system,\n"
    "AMP,Powersoft,Quattrocanali 4804,功放,1,dante;control;analog,channels=4,,,\n"
    "SPEAKER,L-Acoustics,KARA,主扩,4,,impedance_ohm=8;power_w=400,,,\n"
    "SWITCH,Cisco,CBS350-8T,Dante 交换机,1,dante,,,,\n"
)


def _legend_texts(svg):
    import re
    return re.findall(r'<text[^>]*data-layer="LEGEND"[^>]*>([^<]*)</text>', svg)


def test_wire_legend_block_rendered():
    """生成的图幅底部应有「线型说明」表。"""
    svg = _call("/api/run", {"bom": LEG_BOM})["svg"]
    texts = _legend_texts(svg)
    assert "线型说明" in texts, f"缺少线型说明标题，实际：{texts}"
    assert "模拟音频（XLR）" in texts
    assert "Dante 网络音频" in texts
    assert "扬声器功率线" in texts


def test_wire_legend_only_lists_used_signals():
    """只列本图实际出现的信号类型，不出现图中没有的线型。"""
    bom = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
           "SOURCE,,,,会议话筒,2,,,,\n"
           "MIXER,Yamaha,TF5,数字调音台,1,,inputs=32;outputs=16,,,\n"
           "AMP,Powersoft,Q,功放,1,analog,channels=2,,,\n"
           "SPEAKER,L-Acoustics,KARA,主扩,2,,impedance_ohm=8;power_w=400,,,\n")
    texts = _legend_texts(_call("/api/run", {"bom": bom})["svg"])
    assert "模拟音频（XLR）" in texts
    assert "扬声器功率线" in texts
    assert "Dante 网络音频" not in texts, "本图无 Dante 连接，不应列出"


def test_wire_legend_backup_row_when_redundant():
    """有主备冗余时，线型说明里应出现「（备用）」条目。"""
    svg = _call("/api/run", {"bom": LEG_BOM, "redundancy": "FULL_CHAIN"})["svg"]
    texts = _legend_texts(svg)
    assert any("（备用）" in t for t in texts), f"未出现备用线型说明，实际：{texts}"


def test_wire_legend_does_not_break_label_count():
    """线型说明的样例线用 Line 画，不应被计入「连线/线标数量一致性」。"""
    svg = _call("/api/run", {"bom": LEG_BOM})["svg"]
    n_wires = svg.count("<polyline")
    n_labels = svg.count('data-layer="WIRE_LABELS"')
    assert n_wires > 0 and n_wires == n_labels, f"连线 {n_wires} / 线标 {n_labels}"


# ---------- 图例库：永久文档（非缓存）+ 优先级 ----------
def test_legend_library_written_as_permanent_document(monkeypatch, tmp_path):
    """确认图例后立即落盘为永久文档：带 schema / revision / 维护时间。"""
    import avcad.workflow.legend_store as ls
    lib = tmp_path / "legend_library.json"
    monkeypatch.setattr(ls, "DEFAULT_CACHE", lib)

    st = ls.LegendStore()
    lg = ls.Legend(brand="Shure", model="ULXD4D", category="WIRELESS_RX",
                   ports=[ls.LegendPort(signal="XLR", role="out", count=2, label="OUT")])
    st.put(lg); st.save()
    assert lib.exists(), "确认后必须立刻落盘"

    data = json.loads(lib.read_text(encoding="utf-8"))
    assert data["schema"] == ls.SCHEMA
    assert data["count"] == 1
    rec = data["legends"][0]
    assert rec["revision"] == 1 and rec["source"] == "user"
    assert rec["updated_at"] and rec["created_at"]
    assert rec["ports"][0]["count"] == 2


def test_legend_library_revision_increments_and_keeps_history(monkeypatch, tmp_path):
    """每次维护 revision 递增，并把上一版存进 history。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "legend_library.json")

    st = ls.LegendStore()
    for n in (2, 6):
        lg = ls.Legend(brand="Yamaha", model="TF5", category="MIXER",
                       ports=[ls.LegendPort(signal="XLR", role="in", count=n, label="IN")])
        st.put(lg); st.save()

    again = ls.LegendStore()
    rec = again.get("Yamaha", "TF5", "MIXER")
    assert rec.revision == 2, f"第 2 次维护，revision 应为 2，实际 {rec.revision}"
    assert rec.ports[0].count == 6, "应取最后一次维护的值"
    assert len(rec.history) == 1, "应保留 1 条历史"
    assert rec.history[0]["ports"][0]["count"] == 2, "历史里是上一版的值"


def test_legend_put_key_includes_category(monkeypatch, tmp_path):
    """键必须含 category：同 brand+model 不同类别不能互相覆盖。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "legend_library.json")
    st = ls.LegendStore()
    st.put(ls.Legend(brand="", model="", category="SOURCE",
                     ports=[ls.LegendPort(signal="XLR", count=1, label="OUT")]))
    st.put(ls.Legend(brand="", model="", category="WIRELESS_MIC",
                     ports=[ls.LegendPort(signal="RF", count=1, label="ANT")]))
    st.put(ls.Legend(brand="", model="", category="SPEAKER",
                     ports=[ls.LegendPort(signal="SPEAKER", count=2, label="SPK")]))
    st.save()
    again = ls.LegendStore()
    assert again.get("", "", "SOURCE").ports[0].signal == "XLR"
    assert again.get("", "", "WIRELESS_MIC").ports[0].signal == "RF"
    assert again.get("", "", "SPEAKER").ports[0].signal == "SPEAKER"
    assert len(again.all()) == 3, f"三条都应保留，实际 {len(again.all())}"


def test_legend_library_beats_engine_inference(monkeypatch, tmp_path):
    """图例库优先级高于引擎推断：出图端口以库里的最后一次维护为准。"""
    import avcad.workflow.legend_store as ls
    monkeypatch.setattr(ls, "DEFAULT_CACHE", tmp_path / "legend_library.json")
    bom = ("设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n"
           "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n")

    before = _call("/api/run", {"bom": bom})
    inferred = len([d for d in before["devices"] if d["model"] == "TF5"][0]["ports"])

    # 写入一条与推断值不同的图例
    _call("/api/legend", {"action": "put", "brand": "Yamaha", "model": "TF5",
                          "category": "MIXER",
                          "ports": [{"signal": "XLR", "role": "in", "side": "left",
                                     "count": 5, "label": "IN", "air": False}]})
    after = _call("/api/run", {"bom": bom})
    got = len([d for d in after["devices"] if d["model"] == "TF5"][0]["ports"])
    assert inferred != 5, "用例前提：推断值不等于 5"
    assert got == 5, f"应以图例库为准（5），实际 {got}（引擎推断为 {inferred}）"


def test_legend_list_reports_library_info():
    """/api/legend list 返回图例库概览（路径 / 条数）。"""
    r = _call("/api/legend", {"action": "list"})
    lib = r.get("library") or {}
    assert lib.get("path", "").endswith("legend_library.json")
    assert isinstance(lib.get("count"), int)
