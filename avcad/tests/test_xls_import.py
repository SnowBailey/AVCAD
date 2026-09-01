"""回归：导入清单支持 .xls（Excel 97-2003，openpyxl 不支持，走 xlrd）。

早前只有 .xlsx 能导入，.xls 在 build_entries / parse_bom 里直接抛错。
"""
import os

import pytest

pytest.importorskip("xlrd")  # .xls 读取依赖

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample_bom.xls")


def test_build_entries_reads_xls():
    from avcad.workflow.importers import build_entries
    entries, dropped = build_entries(FIX)
    models = [e["model"] for e in entries]
    assert models == ["LA212", "QU16", "UM2000ASD", "T904"], models
    # 无表头的杂项 sheet 应被过滤，不混入主清单
    assert all(e.get("_sheet") == "设备清单" for e in entries)
    # 类别推断在 .xls 路径同样生效
    cats = {e["model"]: e.get("category") for e in entries}
    assert cats["UM2000ASD"] == "ANT_COMBINE"
    assert cats["QU16"] == "MIXER"


def test_parse_bom_reads_xls():
    from avcad.parse.bom_parser import parse_bom
    rows = parse_bom(FIX)
    assert len(rows) == 4
    brands = {r.get("品牌") or r.get("brand") for r in rows}
    assert "IPS" in brands


def test_xls_xlsx_parity():
    """同一份数据，.xls 与 .xlsx 经 build_entries 应得到一致结果。"""
    import openpyxl
    import tempfile

    from avcad.workflow.importers import build_entries

    # 从夹具读取原始行，写成等价 .xlsx
    from avcad.parse.excel_io import read_workbook_sheets
    sheets = read_workbook_sheets(FIX)
    raw = next(iter(sheets.values()))
    wbx = openpyxl.Workbook()
    ws = wbx.active
    for r in raw:
        ws.append(r)
    xlsx = tempfile.mktemp(suffix=".xlsx")
    wbx.save(xlsx)
    try:
        e_xls, _ = build_entries(FIX)
        e_xlsx, _ = build_entries(xlsx)
        assert [e["model"] for e in e_xls] == [e["model"] for e in e_xlsx]
        assert [e.get("category") for e in e_xls] == [e.get("category") for e in e_xlsx]
    finally:
        os.unlink(xlsx)
