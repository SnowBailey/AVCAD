"""AVCAD 命令行入口。"""
from __future__ import annotations
import argparse
import os
import sys

from avcad.parse.bom_parser import parse_bom
from avcad.core.build import build_project, generate_candidates
from avcad.render.draw import draw_devices, draw_wires
from avcad.render.primitives import Canvas
from avcad.render.svg_render import render_svg
from avcad.render.dxf_render import render_dxf
from avcad.workflow.run import run_workflow, summarize


def _draw(proj):
    c = Canvas()
    draw_devices(c, proj)
    draw_wires(c, proj)
    return c


def _ensure_parent(path: str):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)


def cmd_generate(args):
    entries = parse_bom(args.input)
    if args.out:
        _ensure_parent(args.out)
    if args.svg:
        _ensure_parent(args.svg)
    proj = build_project(entries, name=args.name or os.path.splitext(os.path.basename(args.input))[0])
    c = _draw(proj)
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(render_svg(c))
        print(f"SVG 预览 -> {args.svg}")
    if args.out:
        render_dxf(c, args.out, project_name=proj.name)
        print(f"DXF 导出 -> {args.out}")
    _report(proj)


def cmd_candidates(args):
    entries = parse_bom(args.input)
    os.makedirs(args.outdir, exist_ok=True)
    projs = generate_candidates(entries, name=args.name or "AV")
    for label, p in projs:
        if p is None:
            print(f"[跳过] {label} (生成失败)")
            continue
        safe = "_".join(ch if ch.isalnum() else "_" for ch in label.split()).strip("_")
        svg = os.path.join(args.outdir, f"candidate_{safe}.svg")
        c = _draw(p)
        with open(svg, "w", encoding="utf-8") as f:
            f.write(render_svg(c))
        print(f"{label}: {svg}  (问题 {sum(1 for i in p.issues if i.level=='ERROR')} 错误 / "
              f"{sum(1 for i in p.issues if i.level=='WARN')} 警告)")
    print(f"候选已生成于 {args.outdir}")


def _report(proj):
    err = sum(1 for i in proj.issues if i.level == "ERROR")
    warn = sum(1 for i in proj.issues if i.level == "WARN")
    print(f"设备 {len(proj.instances)} 台 | 连线 {len(proj.connections)} 条 | "
          f"交换机 {len(proj.switches)} 台 | 错误 {err} / 警告 {warn}")
    for i in proj.issues:
        if i.level == "ERROR":
            print(f"  [ERROR] {i.code}: {i.msg}")


def cmd_workflow(args):
    """清单驱动 5 步工作流：解析→模块确认→图例回填→架构选择→构建→校验→出图。"""
    ext = os.path.splitext(args.input)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        # Excel 清单：经富集层（类别推断/吊架排除/参数抽取）转为规范化 BOM
        from avcad.workflow.importers import build_entries, to_bom_csv
        entries, dropped = build_entries(args.input)
        if dropped:
            print(f"[排除] {len(dropped)} 个非信号设备（吊架等）：" +
                  ", ".join(str(d.get('设备名称') or d.get('名称')) for d in dropped))
        text = to_bom_csv(entries)
    else:
        text = open(args.input, encoding="utf-8-sig").read()
    decisions = {}
    if args.decisions:
        import json as _json
        decisions = _json.loads(args.decisions)
    result = run_workflow(text, decisions=decisions or None,
                          redundancy=args.redundancy, name=args.name)
    print(summarize(result))
    proj = result["project"]
    if args.svg:
        _ensure_parent(args.svg)
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(render_svg(_draw(proj)))
        print(f"SVG 预览 -> {args.svg}")
    if args.out:
        _ensure_parent(args.out)
        render_dxf(_draw(proj), args.out, project_name=proj.name)
        print(f"DXF 导出 -> {args.out}")


def build_parser():
    p = argparse.ArgumentParser(prog="avcad", description="音视频系统图自动生成引擎")
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("generate", help="由清单生成 DXF 系统图")
    g.add_argument("--input", "-i", required=True, help="清单 .xlsx/.csv")
    g.add_argument("--out", "-o", help="输出 .dxf")
    g.add_argument("--svg", "-s", help="同时输出 SVG 预览")
    g.add_argument("--name", "-n", help="工程名称")
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("candidates", help="生成 3 个候选拓扑预览")
    c.add_argument("--input", "-i", required=True)
    c.add_argument("--outdir", "-d", default="candidates")
    c.add_argument("--name", "-n", default="AV")
    c.set_defaults(func=cmd_candidates)

    u = sub.add_parser("ui", help="启动 Web UI（三步流程）")
    u.add_argument("--port", "-p", type=int, default=8000)
    u.add_argument("--no-browser", action="store_true")
    u.set_defaults(func=cmd_ui)

    w = sub.add_parser("workflow", help="清单驱动 5 步工作流（解析→确认→图例→架构→出图）")
    w.add_argument("--input", "-i", required=True, help="清单 .xlsx / .csv（xlsx 自动富集：类别推断/吊架排除/参数抽取）")
    w.add_argument("--decisions", "-d", help="模块决策 JSON，如 '{\"ULXD4D\":\"exclude\"}'")
    w.add_argument("--redundancy", "-r", default=None,
                   choices=["PROCESSOR_BACKUP", "LINK_BACKUP", "FULL_CHAIN"])
    w.add_argument("--name", "-n", default="AV System")
    w.add_argument("--svg", "-s", help="输出 SVG 预览")
    w.add_argument("--out", "-o", help="输出 DXF")
    w.set_defaults(func=cmd_workflow)
    return p


def cmd_ui(args):
    from avcad.ui.app import main as ui_main
    import sys
    sys.argv = ["avcad-ui", f"-p{args.port}"] + (["--no-browser"] if args.no_browser else [])
    ui_main()


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
