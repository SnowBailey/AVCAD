# AUDIX Performance 系列 · 无线系统连接逻辑

> 资料来源：
> - Audix 官方《AP Series Wireless Brochure》audixusa.com（R41/R42 与 R61/R62 完整规格表）
> - Audix《AP41 & AP42 / AP61 & AP62 Wireless System User Guide》
> - B&H / ProAcoustics / AV-iQ 的 ADS48、ANTDA4161、R62KIT 产品页
> - 阳哥 2026-08-30 口述确认
>
> 状态：**主库已按此整理（2026-08-30），待阳哥复核**

---

## 1. 型号谱系（阳哥口径 + 官方印证）

| 系列 | 接收机 | 官方接收系统 | 通道 | 分集类型 |
|---|---|---|---|---|
| AP41 | R41 | **Single tuner, diversity**（单调谐器，分集式） | 单通道 | 分集 |
| AP42 | R42 | Single tuner, diversity | **双通道** | 分集 |
| AP61 | R61 | **Single tuner, true diversity**（真分集式） | 单通道 | **真分集** |
| AP62 | R62 | Single tuner, true diversity | **双通道** | **真分集** |

官方规格差异（R41/R42 vs R61/R62）：

| 项 | R41 / R42 | R61 / R62 |
|---|---|---|
| 带宽 | 32 MHz | 64 MHz |
| 预设频率 | 106 | 207（手动 2560，25 kHz 步进） |
| 接收距离 | 300 ft / 91 m | **450 ft / 137 m** |
| 同场兼容系统 | 16（R41）/ 8（R42） | 24（R61）/ 12（R62） |
| 信噪比 | 105 dB | 112 dB |
| THD | ≤0.7% | ≤0.4% |

---

## 2. 三条链路

### 链路 A：接收机（AP41/42/61/62 与 R41/42/61/62 KIT）

> 官方（B&H R62KIT）：「Antenna Connector **2 × BNC**（8 VDC, 150 mA）」、
> 「Outputs **2 × XLR 3-pin balanced**、**2 × 1/4" TS unbalanced**」
> 用户手册：「**Both antennas must be installed for the diversity function to work properly.**」

- **天线口：每机固定 2 个 BNC（Antenna A / B），与通道数无关**
  ⚠️ 这与 IPS UM2002（真分集双通道 = 4 口）**不同**：AUDIX 双通道机型内部共享 A/B 两路天线。
- **音频输出：每通道 1×XLR 平衡 + 1×1/4" TS 非平衡**
  （不是 IPS 那种"2 路 XLR + 整机 1 路 6.35 混合"）
- 输出电平：XLR −12 dBV / 1/4" −18 dBV @ 25 kHz 频偏；XLR 可调 −12～+9 dBu

### 链路 B：外置天线 ANTDA4161

> 「Wide-band **active directional** antennas，**Pair**… For ADS 48 Antenna Distribution System」

- **成对出售 / 使用**（Pair）
- 有源**指向**天线（对数周期，心形指向，垂直极化），有效接收角约 **90°**
- 频段 500–700 MHz（部分资料标 522–865）
- 天线增益 6 dB；放大器增益 **5 dB 或 13 dB**（机内 dip 开关）
- 接口 **BNC**，线缆 **RG58 50Ω 同轴线缆**（需另购）
- 可远离接收机 **100 ft（30 m）** 无信号损失

### 链路 C：天线分配器 ADS48

> 官方：「combines up to **four** wireless systems to run off a **single pair of antennas** and one power supply」
> 「combines **4 – two channel systems (8 channels)** with one set of antennas and one power supply」
> 随附「**8× BNC-to-BNC** Coaxial Cables for pass-through connection for up to 4 additional receivers (single or dual)」

- **2 进 / 8 出**（1 对天线进，8 个 BNC 出）
- 单台带 **4 台接收机**（8 ÷ 2 口/台），即 **8 个无线通道**（配 AP42/AP62 双通道机型）
- 频段 520–936 MHz；输入/输出阻抗 **50Ω**；RF 增益 0 dBd ±3 dB
- **内置开关电源**，通过 4 条电源跳线给各接收机供 +12 V DC（每路 800 mA），
  天线口提供 8 V / 150 mA（供有源天线）
- ⚠️ **官方资料未提级联能力** —— 与 IPS UM2000ATD 的「级联端口链式连接」不同。
  超过 4 台接收机时应**另配一套 ADS48 + 天线对**，而不是串级。

---

## 3. 容量测算

| 配置项 | 公式 |
|---|---|
| 单台 ADS48 可带接收机 | `8 ÷ 2 = 4` 台 |
| 所需 ADS48 台数 | `ceil(接收机数 ÷ 4)` |
| 天线对数 | 每套 ADS48 配 **1 对**（2 支） |

对照 IPS：UM2000ATD 单台可带 2 台 UM2002（10 出，非末台留 2 口级联 → 8 ÷ 4）。
AUDIX 台均带机数是 IPS 的 **2 倍**（因为每台接收机只占 2 口而非 4 口）。

---

## 4. 与 IPS 的关键差异（写程序时必须区分）

| 维度 | IPS UM 系列 | AUDIX Performance 系列 |
|---|---|---|
| 每台接收机天线口 | 真分集双通道 = **4** | 任何机型 = **2** |
| 分配器端口 | UM2000ATD **2 进 / 10 出** | ADS48 **2 进 / 8 出** |
| 级联 | **支持**（UM2000ATD，留 2 出） | **不支持**（ADS48，官方未提） |
| 6.35 输出形态 | 整机 **1 路混合**（mix_out） | 每通道 **1 路 TS**（trs_out，与 XLR 并存） |
| 天线 | UM2000AP 全指向 / UM2000AT 有源指向（70°） | ANTDA4161 有源指向（90°），成对 |
| 同轴线 | 50Ω（UM2000AP 赠 6m×2 / UM2000AT 赠 3m×2） | 50Ω RG58（**需另购**） |

程序中的落点：
- `params.antennas`：IPS UM2002 = 4；AUDIX 全部 = 2
- `params.cascade_outs`：IPS UM2000ATD = **2**；AUDIX ADS48 = **0**
  （`ant_dist.yaml` 默认 0，只有明确支持级联的型号才显式设 2）
- `features`：IPS UM2002 用 `mix_out`；AUDIX 用 `trs_out`

---

## 5. 已落盘的主库修正（2026-08-30）

| 型号 | 修正前 | 修正后 |
|---|---|---|
| AP41* / AP61* 套装 | 多为 `WIRELESS_MIC`（发射端） | **`WIRELESS_RX`**，`channels=1`、`antennas=2`、`trs_out`、`set_expand{rx:1, tx:1}` |
| AP42* / AP62* 套装 | 多为 `WIRELESS_MIC` | **`WIRELESS_RX`**，`channels=2`、`antennas=2`、`trs_out`、`set_expand{rx:1, tx:2}` |
| R41KIT A/B | `WIRELESS_RX`（无参数） | 加 `channels=1`、`antennas=2`、`trs_out` |
| R42KIT A/B、R62KIT | `WIRELESS_RX`（无参数） | 加 `channels=2`、`antennas=2`、`trs_out` |
| ADS48 | `params={channels_hint:8, bnc:1}` | **`inputs=2, outputs=8, cascade_outs=0`** |
| ANTDA4161 | `ANTENNA`（OK） | 保持；**成对**，清单单位写「对」时自动 ×2 |

> 与 IPS 同理：套装型号（AP41/42/61/62）核心是上机架、接天线分配器、出 XLR 的**接收机**，
> 必须归 `WIRELESS_RX`；归成 `WIRELESS_MIC` 会排到链路最前端导致无线链断裂。

---

## 6. 待办 / 需阳哥确认

1. **ADS48 是否真无级联**：官方只写「合并 4 套系统」，未提链式扩展。
   若现场确有级联用法，需把 `cascade_outs` 改成 2 并重算容量。
2. **ANTDA4161 的清单单位**：若 BOM 直接写数量 2（支）则无需 ×2；
   若写「1 对」则靠 importer 的 `PAIR_UNITS`（对/副/pair）自动 ×2。
3. **AUDIX 也有 AP41/AP61 的 1/4" 是"混合"还是"每通道"**：
   本文档按 B&H R62KIT 的「2 XLR + 2 1/4"」推断为**每通道各一路**，
   单通道机型（R41/R61）则为 1 XLR + 1 TS。
