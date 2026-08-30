# AVCAD 实施计划与里程碑进度表（v1.1 工作流产品化）

> 编制日期：2026-08-29 ｜ 引擎渲染规则已冻结为 v1.0（`deliverables/system_samples/BASELINE_v1.0/`）
> 开发准则（阳哥指令）：自主采用最优方案、避免就简单细节反复询问；每完成一个模块即自动化测试与校验，发现问题立即修正并再次验证，直至稳定通过。

## 一、背景与范围
渲染引擎（确定性 SVG/DXF 生成）与 A–J 共 10 张拓扑图已**逐张验收确认无误**，渲染规则已冻结。
当前"清单驱动 5 步工作流"仍缺 4 块可产品化的后端能力（设备规格为静态 yaml，无 brand+model 级缓存与回填）：

| 缺项 | 对应工作流步骤 | 本计划模块 |
|---|---|---|
| 图例持久化缓存（brand+model 自动回填） | ④ | M1 LegendStore |
| 模块确认 /「不需要」排除 | ② | M2 ModuleConfirm |
| 图例定义器（端口数量/卡槽自定义） | ③ | M3 LegendBuilder |
| 参考架构模板库 + 最优选择 + SPOF | ⑤ | M4 Architecture |
| 端到端串联 + CLI | — | M5 Workflow.run |
| UI 五步打通 | — | M6 UI |

## 二、里程碑与进度表

| 里程碑 | 内容 | 交付物 | 计划时间节点 | 状态 |
|---|---|---|---|---|
| **M0** | 拓扑结果落盘 + 规则冻结 | `BASELINE_v1.0/` + 清单 + 8 条规则 | 2026-08-29 | ✅ 已完成 |
| **M1** | 图例持久化缓存层 | `workflow/legend_store.py` + 测试 | 2026-08-29 | ✅ 已完成 |
| **M2** | 模块确认 / 排除管线 | `workflow/module_confirm.py` + 测试 | 2026-08-29 | ✅ 已完成 |
| **M3** | 图例定义器 | `workflow/legend_builder.py` + 测试 | 2026-08-29 | ✅ 已完成 |
| **M4** | 参考架构模板库 + 最优选择器 + SPOF | `workflow/architecture.py` + 测试 | 2026-08-29 | ✅ 已完成 |
| **M5** | 端到端工作流管线 + CLI 串联 | `workflow/run.py` + `cli` 扩展 + 集成测试 | 2026-08-29 | ✅ 已完成 |
| **M6** | UI 五步串联 | `ui/app.py` 新端点 + 前端打通 | 2026-08-29 | ✅ 已完成 |
| **M7** | 全套回归 + 发布 v1.1 基线 | 全 pytest 绿 / 十图 PASS / 发布说明 | 2026-08-29 | ✅ 已完成 |
| **M8** | 处理器前置/后置链路逻辑（阳哥规则） | `topology/chain.py` 重写 + `wires/router.py` 适配 + `amp.yaml` 加 dsp + 测试 7 个 | 2026-08-29 | ✅ 已完成 |
| **M9** | DXF 模块标题水平居中（MTEXT 方案） | `dxf_render.py` 将居中/右对齐 `Text` 改为 `MTEXT` 输出，用 `attachment_point` 做 `BOTTOM_CENTER`/`BOTTOM_RIGHT`；`_draw_title` 保持模块顶部三行标题 | 2026-08-29 | ✅ 已完成 |
| **M10** | 完整交互页面（5 步向导）+ 速度优化 | `ui/app.py` 重写为完整 API（parse/architectures/run/validate/export/legend）+ `index.html` 重写为完整 5 步向导（文件拖拽/模块确认/图例确认/架构选择/生成导出）；xlsx 仅解析一次复用 CSV + 进程内解析缓存 + ezdxf C 扩展自动启用 | 2026-08-29 | ✅ 已完成 |
| 缓冲/迭代 | 用户确认与微调 | — | 2026-09-07 → 09-12 | ⏳ 待开始 |
| **DMG 打包** | 桌面安装包（待页面确认后） | PyInstaller 文件夹模式 → .app + `dmgbuild`/`hdiutil` 封装；`static/` 随包捆绑 | 待阳哥确认页面无问题后 | ⏳ 待开始 |

> **实际进度**：M0–M10 全部于 **2026-08-29** 单轮自主完成并通过循环验证。M10 交付**完整交互页面**：`ui/app.py` 完整 5 步 API + `index.html` 完整 5 步向导（文件拖拽导入 / 模块确认 / 图例确认 / 架构选择 / 生成导出，含实时排版校验徽章与 DXF 导出）。速度优化：xlsx 仅解析一次复用规范化 CSV、进程内解析缓存、ezdxf C 扩展。验证：全量 **61 passed**（含 7 个 UI API 测试）；`scripts/verify_ui_8x.py` 对真实清单完整管线循环 **8 次**全部 PASS（overlap=0/diagonal=0 完全一致，平均 ~51ms/轮）；jsdom 驱动前端 5 步流程**无 JS 运行时异常**。DMG 打包待阳哥确认页面无问题后执行。

## 三、循环验证机制（贯穿全程）
1. 每模块实现后，立即补 `avcad/tests/test_workflow_*.py` 单测。
2. 运行 `pytest avcad/tests -q` 与 `scripts/check_overlap.py`（针对受影响图）必须全绿。
3. 失败则就地修复并重跑，直至稳定通过；不遗留 `xfail`/`skip` 掩盖问题。
4. 每个里程碑结束在本文档进度表回写状态（✅/🔄/⏳）。

## 四、技术决策（已采纳，无需确认）
- 缓存文件：`avcad/data/legend_cache.json`，原子写（写临时文件后 `os.replace`）。
- 缓存键：`(brand or "_generic", model or "_")`；无品牌设备走 `_generic` 命名空间。
- `workflow` 为新增子包，纯标准库 + 现有 `model/specs`、`core/build`、`validate/checks`，不引入新依赖。
- 端到端 `run()` 为幂等管线：解析 → 模块确认 → 图例回填 → 架构选择 → 构建 → 校验 → 出图，并返回 `cache_miss`（供 UI 提示定义图例）。

## 五、逻辑迭代（2026-08-29 晚 · 阳哥反馈）

### M8 处理器前置/后置链路（阳哥规则）
- **规则**：处理器默认放在调音台**之前**（SOURCE → 处理器 → 调音台 → 功放 → 扬声器）；可指定 `position=后置` 放在调音台之后；**功放带 DSP 或处理器参与主备冗余时强制前置**。
- **实现**：
  - `topology/chain.py` 重写为数据驱动：新增 `PROC_PRE`/`PROC_POST` 两个独立 stage（`build_chain` 按位置生成核心级 `[PROC_PRE]→MIXER→[PROC_POST]`；`assign_stages` 据此置 `inst.stage`）；`processor.yaml` 去掉默认 `proc_func: system`，使默认落到前置。
  - 位置判定优先级：`amp_has_dsp`/冗余 → 显式 `position` 参数 → `proc_func`(automix=前置, system=后置) → 默认前置。
  - `wires/router.py`：`_connect_sources_to_core` 与 `_handle_speakers` 的 `"PROCESSOR"` 硬编码改为识别 `PROC_PRE`/`PROC_POST`；有源扬声器取"最后一个线路级"作为前级（兼容前置/后置混排）。
  - 功放 DSP 检测（`_amp_has_dsp`）：features 含 dsp / params.dsp / proc_func 非空 / 型号含 "DSP" 四重信号；`amp.yaml` 的 `features_available` 补 `dsp`（避免 expand_instance 按 features_available 过滤把用户写的 `特性=dsp` 丢掉）。
- **验证**：新增 `test_workflow_chain_position.py` 7 测全绿（默认前置/显式后置/功放DSP强制前置/proc_func语义/混排/主备强制前置/端到端）；全量 pytest **51 passed**；真实 `测试.xlsx` 重跑 → 链 `音源→处理器(前置)→调音台→功放→扬声器`，2×GMN1208D 均 PROC_PRE，**0 错误 0 警告**，`check_overlap` PASS。

### 工作流澄清：先确认产品接口，再套用拓扑
- 当前 `测试.xlsx` 出图为**拓扑/信号流图**：端口来自易科主库默认推断，**未经逐型号确认**（即"待定义图例 13 个"）。
- 正确顺序（5 步工作流第③步「图例定义」）：先逐个型号确认真实接口（端口/卡槽）→ 固化进 `legend_cache.json` → 拓扑按确认端口出图。
- 已生成 `deliverables/real_runs/product_interface_review.md`（13 个型号级接口定义清单）供阳哥逐项核对；确认后我固化缓存并重跑，即"确认后的产品图套用拓扑"。

### M9 模块标题水平居中（阳哥截图反馈，经两轮修正）
- **第一次反馈**：DXF 模块文字整体偏右/出框。
  - **根因**：SVG 用 `text-anchor`（`start/middle/end`）实现水平对齐，但 `dxf_render.py` 未处理 `Text.anchor`，ezdxf 默认按 `BOTTOM_LEFT` 把插入点当文字左下角。
  - **初修**：按 `p.anchor` 映射 ezdxf `TextEntityAlignment`：`start→BOTTOM_LEFT`、`middle→BOTTOM_CENTER`、`end→BOTTOM_RIGHT`；去掉 `-2` 垂直偏移。ezdxf 内读取对齐为 `BOTTOM_CENTER`，但阳哥在 CAD 中仍见偏右。
- **第二次反馈与联网搜索**：AutoCAD 2023/2024 对单行 `TEXT` 实体的 `BOTTOM_CENTER` 对齐存在已知 bug（Autodesk 官方论坛），文字会随机偏移到一侧；官方/社区建议改用 `MTEXT` 或在更高版本格式保存。
- **终版修复**：
  - `dxf_render.py` 将所有 `Text`（模块标题、端口标签、项目标题）改用 `MTEXT` 输出，按 `p.anchor` 映射 `attachment_point`：`start→MTEXT_BOTTOM_LEFT(7)`、`middle→MTEXT_BOTTOM_CENTER(8)`、`end→MTEXT_BOTTOM_RIGHT(9)`。
  - `_draw_title()` 保持标题三行钉在模块顶部标题区（`inst.y + 10/20/29`），只做水平居中，不做上下居中。
- **验证**：更新 `test_render_dxf_text.py` 断言 MTEXT `attachment_point`；全量 pytest **54 passed**；重跑 `测试.xlsx` → 0 错误 0 警告，`check_overlap` PASS；DXF 内 157/157 个模块标题在其模块内水平居中（偏差<3px），`LABELS` 层无 `TEXT` 实体，全为 `MTEXT`。
