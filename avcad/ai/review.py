"""拓扑 AI 审查层（确定性，不依赖外部 LLM）。

基于校验结论与拓扑特征，以对话方式给出优化建议。只建议、绝不反向改图。
如需接入真实 LLM，可在本层替换为模型调用，但建议仍以校验结论为准。
"""
from __future__ import annotations
from avcad.model.schema import Redundancy
from avcad.validate.checks import dante_primary_backup_switch_overlap


def review_project(proj) -> list:
    out = []
    errors = [i for i in proj.issues if i.level == "ERROR"]
    warns = [i for i in proj.issues if i.level == "WARN"]
    infos = [i for i in proj.issues if i.level == "INFO"]

    if errors:
        out.append(("严重", f"当前有 {len(errors)} 个阻断性问题，必须修复后才能出图："
                          + "；".join(f"[{e.code}] {e.msg}" for e in errors[:5])))
    else:
        out.append(("通过", "未检出阻断性问题，可进入拓扑候选与出图环节。"))

    cats = {i.category for i in proj.instances}
    has_red = any(i.redundancy != Redundancy.NONE for i in proj.instances)
    has_dante = any(p.signal.name == "DANTE" for i in proj.instances for p in i.ports)

    if has_dante and not has_red:
        out.append(("建议", "系统含 Dante 网络但无主备。关键会议/演出场景建议对调音台或处理器做主备，"
                          "并采用冗余 Dante 双交换机（Primary/Secondary 两网绝不互连）。"))
    if has_dante and has_red and dante_primary_backup_switch_overlap(proj):
        out.append(("提示", "主备 Dante 设备共用同一台交换机，存在 SPOF。建议增设备用交换机做冗余 Dante，主备分别接入 Primary/Secondary 两网。"))
    if not has_red and ("MIXER" in cats or "PROCESSOR" in cats):
        out.append(("可选", "若本系统为重要固定安装，可考虑 PROCESSOR_BACKUP / FULL_CHAIN 冗余策略提升可用性。"))

    # 无线真分集
    if "WIRELESS_RX" in cats:
        rx = [i for i in proj.instances if i.category == "WIRELESS_RX"]
        for r in rx:
            ant = [p for p in r.ports if p.signal.name == "RF" and p.role == "in"]
            if len(ant) < 2:
                out.append(("严重", f"无线接收机 {r.name} 不满足真分集（需≥2路天线输入）。"))
        out.append(("提示", "无线子系统已按真分集架构绘制（发射→有源天线A/B→分配器→接收机）。"
                          "小系统可折叠天线中继/分线器以简化图纸。"))

    # 功放扬声器匹配
    for w in proj.meta.get("amp_warnings", []):
        msg = w[2] if isinstance(w, tuple) and len(w) == 3 else str(w)
        out.append(("建议", f"功放匹配：{msg}。建议调整扬声器分组或功放通道阻抗/功率配置。"))

    # 余量提示
    spare = [i for i in infos if i.code == "UNMET_IN"]
    if spare:
        out.append(("提示", f"存在 {len(spare)} 处输入余量（设备端口数多于上游信号），属正常设计余量，可保留或按需精简型号。"))

    # 有源扬声器
    if any(i.category == "SPEAKER" and i.active for i in proj.instances):
        out.append(("提示", "检测到有源扬声器，已按'信号线直连、跳过独立功放'规则处理；"
                          "请确认其供电与吊挂/安装条件已单独设计。"))

    if not out:
        out.append(("通过", "拓扑结构合理，无显著优化点。"))
    return [{"level": lv, "text": tx} for lv, tx in out]
