# AVCAD · 易科型号库接入 + 出图标准（音频 V1）— 本轮变更

> 数据源：`260717-易科国际-产品资料清单V49.xlsx`（38 品牌表 / 2298 产品）
> 解析：`scripts/build_catalog.py`（非流式批量）→ `avcad/data/eko_catalog.json`
> 匹配：`avcad/data/catalog_resolver.py` + `avcad/parse/product_resolver.py`（确定性，无 LLM）
> 出图：`avcad/render/*` + `scripts/render_samples.py`
>
> 最新自测：`system.svg` 实例 16 / 连线 25 / 交换机 1；模块重叠 0 处 / 斜线段 0 条；pytest 8 passed。

## 本轮按阳哥要求落实的修正

### 1. 连线精确避让模块 + 无悬空端口
- **问题**：用户截图显示连线仍穿过/盖住模块，且交换机端连线落在模块中心而非真实端口。
- **修复 1（端口精度）**：`draw.py::_endpoint()` 中交换机连接不再返回模块几何中心，而是返回第一个可用 **Dante 端口**的实际坐标，确保每根线两端都接在真实接口上。
- **修复 2（全路径避让）**：`draw.py::_route_avoid()` 改为对整条折线的**所有线段**做障碍检测（而非仅中段）；中段在 x/y 方向搜索 ±340px 无遮挡通道；并增加「方向互换」兜底（竖直通道被挡则改水平绕边，反之亦然）。
- **修复 3（跳线不产生斜线）**：发现 `_bump()` 原实现把被跳线段的终点误丢弃，导致跳线后接到下一节点产生斜线。已修正为 `points[k+1:]` 保留原终点，跳线全部保持横平竖直。
- **验证**：`scripts/check_overlap.py` 扫描 system.svg → **模块重叠 0 处 / 斜线段 0 条**（17 模块 / 25 连线）。

### 2. 型号库精确覆盖：IPS CF6300 / 6300WB / CF6804
- **CF6300**（手拉手会议主机）→ 重分类为 **IO**，`ports_override`：
  - 右侧 4×凤凰端子分区输出（PHX1-4）+ 1×XLR MIX 输出。
- **CF6300WB**（无线会讨天线板）→ 重分类为 **IO**，`ports_override`：
  - 左侧 4×6P_DIN 音频输入（DIN1-4）+ 1×XLR 输入；
  - 顶部通信口 RJ45 / UPDATE / RS485 / RS232。
- **CF6804**（四通道无线会议系统）→ 保持 `WIRELESS_RX`，改用 `ports_override`：
  - 1×XLR MIX out + 4×XLR 独立输出。
- 更新位置：`eko_catalog.json` 直接覆盖 + `scripts/build_catalog.py` 的 `MANUAL_CAT` / `MANUAL_PARAMS`，保证后续重建不丢失。

### 3. ALLEN&HEATH 仅保留 QU16
- 用户指定：ALLEN&HEATH 只画 QU16，其余型号不打图。
- 实现：`build_catalog.py::_apply_manual()` 中对 `brand == ALLEN&HEATH` 且 `model != QU16` 的产品强制 `category=None, defer_reason="ALLEN&HEATH 仅 QU16 出图"`。
- 结果：catalog 中 128 个 ALLEN&HEATH 非 QU16 型号已全部后置，QU16 保留为 MIXER。

### 4. 型号确认 Web 进度表
- 交付物：`deliverables/model_previews/index.html`（自包含，可本地浏览器打开）。
- 覆盖 7 个品牌（IPS → ezacoustics → EAW → Powersoft → Symetrix → AUDIX → YAMAHA），共 1190 个唯一型号。
- 每个型号预生成单设备 SVG 预览（859 个可出图，其余为配件/线缆/未识别等）。
- 功能：按品牌 tab 浏览、搜索型号/名称、筛选（全部/待确认/已确认/已跳过/仅可出图/仅后置）、✓ 确认、✗ 不需要、重置、进度条、localStorage 自动保存、导出/导入 JSON。

---

# AVCAD · 易科型号库接入 + 出图标准（音频 V1）— 历史变更

> 数据源：`260717-易科国际-产品资料清单V49.xlsx`（38 品牌表 / 2298 产品）
> 解析：`scripts/build_catalog.py`（非流式批量）→ `avcad/data/eko_catalog.json`
> 匹配：`avcad/data/catalog_resolver.py` + `avcad/parse/product_resolver.py`（确定性，无 LLM）
> 出图：`avcad/render/*` + `scripts/render_samples.py`

## 本轮按阳哥要求落实的修正

### 1. 品牌剔除（不画出图）
- **Green-GO**：无线内通系统，基本不用 → 分类为「通讯(V2)」且 `drawable=False`。
- **Community / Apart**：指定不画 → 仍正确识别类别（扬声器等），但 `drawable=False`。
- 实现：`catalog_resolver.DRAW_EXCLUDE_BRANDS = {"GREEN-GO","COMMUNITY","APART"}`，
  `resolve()` 的 `drawable` 在「有模板」基础上再排除这些品牌；报告与样本画廊同步尊重。

### 2. 名称即语义（不只看品牌型号参数）
- 规则：`声卡 / 音频接口 / interface / I/O 箱 / 接口箱 / 扩展接口` → **IO** 类，
  且该判定置于「话筒/会议单元→SOURCE」之前，避免「多通道声卡」被误判为音源。
- 验证：**YAMAHA RUio16-D**（多通道声卡）→ IO ✅

### 3. 关键产品参数校正（人工覆盖 `MANUAL_PARAMS` / `MANUAL_CAT`）
- **IPS CF6804**（四通道无线会议系统）：输出 = `1 路 XLR 混合输出 + 4 路 XLR 独立输出`
  （`xlr_mixed_out=1, xlr_independent_out=4, channels=4`）。
- **YAMAHA CS-R10**（数字调音台台面）：本地 I/O `8 模拟输入 / 8 模拟输出`，
  扩展槽 `HY×4, MY×2`，搭配扩展 I/O 机架 **RPio622**（`expansion="RPio622"`）。
- **YAMAHA RY16-AE**：插到 RPio 卡槽里的 AES 输入/输出卡 → 归 **IO**（原为 PROCESSOR）。

### 4. YAMAHA PM / DM 系列链路理解（已上网核对官方资料）
- **RIVAGE PM（PM 系列）**：CS-R10 台面（8in/8out, MY×2）→ DSP 引擎 DSP-RX（HY×4，每槽 256 I/O）→
  I/O 机架 RPio622（6× RY 卡槽，如 RY16-AE AES 卡）/ RPio222，经 TWINLANe 低延迟网络；
  Dante 经 HY/Dante 卡 → 交换机。台面与引擎分离是该类系统的关键拓扑。
- **DM 系列**：DM7（本地 32in/16out，Dante 144×144，1× PY 槽）↔ Rio3224-D3/Rio1608-D3
  舞台接口箱，经 Dante 连接（冗余星型经交换机 / Daisy-Chain 菊花链）。
- 上述事实已写入型号库参数，供后续精确出图（台面+引擎+接口箱链路）使用。

### 5. 出图标准：所有连线均避让模块 + 标准交叉处理
- `avcad/render/draw.py` 重写 `draw_wires`：
  - **所有连线（含模拟音频 XLR / AES / SPEAKER，不止网络信号）** 路由时**避让设备模块**——
    平移中段到一条不与任何设备矩形重叠的「通道」，始终保持正交（横平竖直）。
  - **交叉处按标准制图做跳线**：低优先级线（网络/控制 < 音频）在交点拱起小矩形桥（hop），
    高优先级线保持连续。优先级：`XLR/AES/SPEAKER=5 > RF/OPTICAL=4 > DANTE=3 > IP/RS232/GPIO=2 > POWER=1`。
- `_route_avoid` 重构：显式接收 `first`（h/v）方向，按中段类型在 x 或 y 方向搜索无遮挡通道；
  搜索范围扩至 ±300px（原 ±48px），可避开 RPio622 这类 177px 宽的大模块；
  并修复**端点等高/等宽导致中段退化、进而产生斜线**的 bug（中点计算改用目标端点坐标）。
- 单元验证：`_bump`（水平拱上 / 垂直拱右）、`_route_avoid`（中段成功平移避开矩形）均通过。
- 出图自测（`scripts/check_overlap`，扫描 system.svg）：**模块重叠 0 处 / 斜线段 0 条**
  （17 个模块矩形、26 条连线）。

## 新增：设备出图样本 V3（卡槽语义 / 三行标题 / 交换机重绘 / 线不盖模块）

### 1. 重新梳理卡槽设备（HY/MY/RY 不是对外接口）
- **YAMAHA CS-R10**：固定本地 I/O 只有 `8 模拟输入 / 8 模拟输出`；`HY×4 / MY×2` 是插接口卡的槽位，
  未插卡时不对外。DANTE / CTRL 等网络能力由卡槽提供，故 `feature_ports=False` 关掉模板按 feature 自动生成的
  DANTE/CTRL 端口，模块底部以卡槽条可视化（HY1-HY4、MY1-MY2）。**实测端口数 = 16（8+8）**。
- **YAMAHA RPio622**：整机就是卡槽机架，未插卡时无对外接口（`ports_override=[]` 清空端口）；
  模块底部绘制 `RY1-RY6 / HY1-HY2 / MY1-MY2` 卡槽条，不生成 XLR 输入输出端口。**实测端口数 = 0**。
- **YAMAHA RY16-AE** 等接口卡：单独作为 IO 设备出图（16 通道 AES/EBU I/O）。
- 参考 **Symetrix Edge / Radius** 等带卡槽 DSP 的画法：卡槽以独立小格在模块底部平铺展示。

### 2. 补齐关键型号真实接口（上网查规格后人工覆盖）
- **YAMAHA RMio64-D**（Dante/MADI 转换器）：左 `DANTE-P/S`，右 `MADI-IN/OUT(BNC+光纤)`，顶 `WCLK`。**实测端口数 = 7**。
- **EAW UX3600**（扬声器处理器）：`3 路 XLR 模拟输入 / 6 路 XLR 模拟输出`。
  注意：音箱管理器模板以 `zones` 参数化（默认 4 区），不读取 `inputs/outputs`；故 UX3600 改用 `ports_override`
  显式固定为 3 IN + 6 OUT（模拟 XLR）。**实测端口数 = 9**（此前因走 zones 模板误渲染为 6）。

### 3. 模块标题改为「名称 + 品牌 + 型号」三行
- 第一行：设备名称（来自清单 / 规格名）；第二行：品牌；第三行：型号。
- 标题区高度从 20px 增至 32px，确保三行不重叠。

### 4. 交换机不再挤成一坨
- 端口由顶部单排改为**上下两排**均匀分布；交换机高度增加以容纳双排端口与三行标题。
- 单设备样本 `SWITCH · YAMAHA SWP2-10MMF` 文字与端口不再重叠。

### 5. 系统拓扑图：连线不盖住模块
- 渲染顺序：**模块主体 → 连线 → 端口**（端口始终在最上层，连线终点清晰）。
- 关键在**连线路由本身避让模块**：`draw_wires` 对所有连线做 `_route_avoid`（中段平移到无遮挡通道），
  因此连线从几何上不穿过任何模块矩形，而不是靠绘制顺序「盖住」模块。
- 本轮修复：`_route_avoid` 原仅对网络线避让、且存在「端点等高导致中段退化→产生斜线、±48px 不足以避开宽模块」
  两个缺陷，已重构为全连线避让 + 显式方向 + ±300px 通道搜索，并消除斜线。
- **出图自测（system.svg）：模块重叠 0 处 / 斜线段 0 条 / 实例 16 / 连线 26 / 交换机 1**。

## 历史：设备出图样本 V2（模块不重叠 / 连线横平竖直 / 7 品牌覆盖）

### 1. 修复模块重叠
- **根因**：`avcad/layout/engine.py` 在计算列宽前没有先调用 `compute_geometry()`，设备 `w/h` 还是默认值 90，导致列间距被压到 160；真实宽度超过后，列与列之间只剩 4px 缝隙，看起来像重叠。
- **修复**：`place()` 开头先对所有实例 `compute_geometry(d)`，再按真实宽度算 `col_w = max_w + COL_GAP`。
- **结果**：列间距恢复为 70px，模块之间不再贴边/重叠。

### 2. 修复所有连线必须横平竖直
- **根因**：`_bump()` 交叉跳线用斜向小弧线模拟半圆，导致 polyline 出现斜线段。
- **修复**：跳线改为标准制图「矩形桥」—— 水平线被垂直线穿过时向上拱起 6px 再水平走，垂直线被水平线穿过时向右拱起 6px 再垂直走；所有新增段均为水平或垂直。
- **验证**：脚本扫描 system.svg 全部 30 条 polyline，共 0 条斜线。

### 3. 修复功放/扬声器阻抗解析崩溃
- **根因**：`build_catalog.py` 从规格参数提取阻抗时把单值也存成 `[4]` 列表，`amp_match.py` 用 `set(list)` 比较时因 list 不可哈希崩溃。
- **修复**：`extract_params()` 单阻抗存标量、多阻抗才存列表；`amp_match.py` 增加 `_ohm()` 防御式取值。
- **结果**：Powersoft Ottocanali 4K4 → EAW SB210 Black 无源超低可正常匹配并联/串联。

### 4. 拓扑链补齐 IO / AMP 阶段
- **根因**：原 `build_chain()` 会跳过 AMP 阶段（全有源扬声器时）且 IO 从未进入主链，导致 RPio622、功放实例被丢到 `(0,0)` 重叠。
- **修复**：
  - 只要 BOM 中出现 AMP 实例，就保留 AMP 阶段；
  - IO 作为核心设备扩展，放在 MIXER/PROCESSOR 之后；
  - 所有存在的 category 都进入 chain 获得合法 stage。

### 5. 新样本 BOM：覆盖指定 7 品牌
样本集成系统图仅使用 `brand+model+qty`：

| 阶段 | 品牌 | 型号 | 说明 |
|---|---|---|---|
| WIRELESS_MIC | AUDIX | AP41 OM2 A | 无线手持话筒 |
| ANTENNA | AUDIX | ANTDA4161 ×2 | 有源定向天线 |
| ANT_DIST | AUDIX | ADS48 | 天线分配器 |
| WIRELESS_RX | IPS | CF6804 | 四通道无线会议接收机 |
| SOURCE | AUDIX | OM2 ×2 | 有线人声话筒 |
| MIXER | YAMAHA | CS-R10 | 数字调音台台面 |
| PROCESSOR | Symetrix | Jupiter4 | 数字音频处理器 |
| IO | YAMAHA | RPio622 | 扩展接口箱 |
| SPEAKER_MGR | ezacoustics | ESM0408 | 数字音箱管理器 |
| AMP | Powersoft | Ottocanali 4K4 | 8 通道功放 |
| SPEAKER | EAW | SB210 Black ×2 | 无源超低（接功放） |
| SPEAKER | EAW | ANYA V2 Black ×2 | 有源全频（Dante 直联） |

- 系统图实例 16 个，连线 29 条，交换机 1 台。
- 全部 7 个目标品牌均已出图。

### 6. 出图质量自测
- 模块重叠：0 处
- 斜线段：0 条
- pytest：`8 passed`

## 关键数字（本轮重建后）
- 产品 2298｜音频可识别 **1529**｜**可出图 1349**（已剔除 Green-GO/Community/Apart 共 180 件）｜
  已识别但不画（品牌剔除）180｜需人工/后置 769。
- 12 个音频 V1 类别全部有设备模板、可出图（SOURCE/WIRELESS_MIC/ANTENNA/ANT_DIST/WIRELESS_RX/
  MIXER/PROCESSOR/SPEAKER_MGR/AMP/SPEAKER/SWITCH/IO）。
- `pytest 8 passed`。

## 交付物
- `deliverables/catalog_samples/index.html` — 12 类别单设备样本画廊 + 集成系统图（仅 brand+model+qty）
- `deliverables/catalog_samples/system.svg` — 含标准交叉跳线 / 网络线避让的集成系统图
- `deliverables/catalog_report.md` / `catalog_report.json` — 匹配与出图统计
- `deliverables/catalog_report_deferred.csv` — 769 条需人工/后置清单（含类型/处理建议）

## 复用（公司模板更新后）
```bash
python scripts/build_catalog.py "/path/新清单.xlsx"   # 重建型号库（可重跑）
# 出图流程按 品牌+型号+数量 自动匹配，确定性、无 LLM
```
