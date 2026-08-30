"""工程编排：清单 -> 实例 -> 链路 -> 布局 -> 连线 -> 校验 -> 工程对象。含 3 候选拓扑。"""
from __future__ import annotations
from avcad.model.schema import Project, DeviceInstance, Redundancy
from avcad.model.specs import build_instances, load_specs, expand_instance
from avcad.parse.product_resolver import enrich
from avcad.topology.chain import build_chain, assign_stages, pair_redundancy
from avcad.layout.engine import place
from avcad.wires.router import connect
from avcad.validate.checks import validate


def _ensure_wireless_dist(entries):
    cats = {str(e.get("category", "")).upper() for e in entries}
    wireless = bool(cats & {"WIRELESS_MIC", "WIRELESS_RX", "ANTENNA"})
    if wireless and "ANT_DIST" not in cats:
        entries = list(entries) + [{
            "category": "ANT_DIST", "name": "天线信号分配器", "quantity": 1,
            "params": {"inputs": 2, "outputs": 4},
        }]
    return entries


def _make_switches(instances, redundant: bool):
    specs = load_specs()
    spec = specs["SWITCH"]
    n_dante = sum(1 for i in instances for p in i.ports if p.signal.name == "DANTE")
    ports = max(8, min(24, n_dante + 2))
    sw = []
    for k in range(2 if redundant else 1):
        e = {"category": "SWITCH", "name": "Dante 交换机" + ("(备)" if k else ""),
             "params": {"ports": ports}}
        inst = expand_instance(spec, e, k)
        inst.redundancy = Redundancy.LINK_BACKUP if redundant else Redundancy.NONE
        if k == 1:
            inst.is_backup = True
        sw.append(inst)
    return sw


def build_project(entries: list, name: str = "AV System",
                  legend_store=None) -> Project:
    """legend_store: 可选 LegendStore；若提供，实例建成后立即按缓存图例回填端口
    （用户确认过的型号自动套用，未确认则保留规格默认）。"""
    entries = enrich(list(entries))
    entries = _ensure_wireless_dist(entries)
    instances = build_instances(entries)
    if legend_store is not None:
        for inst in instances:
            legend_store.apply(inst)
    chain = build_chain(instances)
    pair_redundancy(instances)
    assign_stages(instances, chain)

    has_dante = any(p.signal.name == "DANTE" for i in instances for p in i.ports)
    redundant_dante = any(i.is_backup and any(p.signal.name == "DANTE" for p in i.ports)
                          for i in instances)
    switches = _make_switches(instances, redundant_dante) if has_dante else []

    proj = Project(name=name, instances=instances, chain=chain, switches=switches)
    size = place(instances, chain, switches)
    proj.meta.update(size)
    connect(proj)
    validate(proj)
    return proj


def _clone_entries(entries):
    out = []
    for e in entries:
        e2 = dict(e)
        e2["params"] = dict(e.get("params", {}))
        e2["features"] = list(e.get("features", []) or [])
        out.append(e2)
    return out


def _apply_redundancy(es, mapping):
    """按冗余映射，将目标类别复制为主/备两台并互指 pair（主备需成对设备）。"""
    out = []
    for e in es:
        cat = str(e.get("category", "")).upper()
        if cat in mapping:
            lvl = mapping[cat]
            p = dict(e)
            p["params"] = dict(e.get("params", {}))
            p["features"] = list(e.get("features", []) or [])
            p["quantity"] = 1
            p["redundancy"] = lvl
            p["uid"] = f"{cat.lower()}_MAIN"
            p["pair"] = f"{cat.lower()}_BAK"
            b = dict(p)
            b["name"] = (e.get("name") or cat) + "(备)"
            b["uid"] = f"{cat.lower()}_BAK"
            b["pair"] = f"{cat.lower()}_MAIN"
            out += [p, b]
        else:
            out.append(e)
    return out


def generate_candidates(entries: list, name: str = "AV System"):
    """确定性生成 3 个候选拓扑（按冗余维度），供用户选择。"""
    base = enrich(list(entries))
    base = _ensure_wireless_dist(base)

    cand = []
    cand.append(("T1 基础（无主备，单链路）", _clone_entries(base)))
    cand.append(("T2 调音台主备（单点冗余）",
                 _apply_redundancy(_clone_entries(base), {"MIXER": "PROCESSOR_BACKUP"})))
    cand.append(("T3 全链路主备（Dante 冗余双交换机）",
                 _apply_redundancy(_clone_entries(base),
                                   {"MIXER": "FULL_CHAIN", "PROCESSOR": "FULL_CHAIN"})))

    projects = []
    for label, es in cand:
        try:
            p = build_project(es, name=f"{name} · {label}")
            p.meta["candidate_label"] = label
            projects.append((label, p))
        except Exception:
            projects.append((label, None))
    return projects
