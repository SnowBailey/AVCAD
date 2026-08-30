# -*- coding: utf-8 -*-
"""会议手拉手链路调试：打印设备、端口、会议相关连线。"""
import sys
sys.path.insert(0, '.')

from avcad.workflow.importers import build_entries
from avcad.core.build import build_project

PATH = '/Users/mac/Desktop/202601/文博-太阳纸业20260806.xlsx'
SHEETS = ['1F会议室', '4F会议室', '2F', '3F']

sheet = sys.argv[1] if len(sys.argv) > 1 else '1F会议室'
entries, _ = build_entries(PATH, sheet=sheet)
p = build_project(entries, name=sheet)

print('CHAIN:', p.chain)
print()
for i in p.instances:
    print('  [{}] {:9s} {:12s} stage={:12s} params={}'.format(
        i.category, i.brand, i.model, i.stage, getattr(i, 'params', None) or {}))
    print('        ports=', [(pp.id, pp.signal.value, pp.role, pp.side) for pp in i.ports])
print('  SWITCH:', [(s.brand, s.model) for s in p.switches])
print()

by = {i.uid: i for i in list(p.instances) + list(p.switches)}
print('--- 会议相关连线 (MIC_HOST / ANTENNA 参与 或 note 含手拉手) ---')
n = 0
for c in p.connections:
    f, t = by[c.from_uid], by[c.to_uid]
    if ('MIC_HOST' in (f.category, t.category) or 'ANTENNA' in (f.category, t.category)
            or '手拉手' in (c.note or '') or f.model.startswith('CF63')
            or t.model.startswith('CF63')):
        n += 1
        print('   {:12s} {:10s} --{:6s}--> {:12s} {:10s}  [{}]'.format(
            f.model, c.from_port, c.signal.value, t.model, c.to_port, c.note))
print('  小计:', n)
