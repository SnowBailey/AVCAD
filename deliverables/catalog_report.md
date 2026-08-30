# 易科国际型号库 · 匹配与出图报告（音频 V1）

> 数据源：`260717-易科国际-产品资料清单V49.xlsx`  
> 生成：非流式批量解析（`scripts/build_catalog.py`）+ 解析器（`avcad/data/catalog_resolver.py`）

## 1. 总体统计

| 指标 | 数值 |
| --- | ---: |
| 产品总数 | 2298 |
| 音频可识别（命中类别） | 1529 |
| **可出图**（有模板且品牌允许） | 1349 |
| 已识别但指定不画（品牌剔除） | 180 |
| 需人工 / 后置 | 769 |

## 2. 音频类别分布（可出图）

| 类别 | 中文 | 数量 | 可出图 |
| --- | --- | ---: | --- |
| SPEAKER | 扬声器 | 557 | ✅ |
| SOURCE | 音源/话筒 | 266 | ✅ |
| MIXER | 调音台 | 127 | ✅ |
| AMP | 功放 | 116 | ✅ |
| PROCESSOR | 处理器 | 105 | ✅ |
| WIRELESS_MIC | 无线话筒 | 58 | ✅ |
| IO | 音频 I/O 箱 | 40 | ✅ |
| SPEAKER_MGR | 扬声器管理器 | 30 | ✅ |
| SWITCH | Dante 交换机 | 19 | ✅ |
| ANTENNA | 天线 | 15 | ✅ |
| WIRELESS_RX | 无线接收机 | 14 | ✅ |
| ANT_DIST | 天线分配器 | 2 | ✅ |

> 注：Apart、Community 已被正确识别类别，但按阳哥要求不画出图（已排除在可出图统计外）。

## 3. 后置 / 未识别分布（需人工）

| 后置原因 | 数量 | 说明 |
| --- | ---: | --- |
| 音频未识别 | 359 | 分类关键词未命中（多为配件/线缆/耗材/非音频） |
| 音频配件(需人工) | 214 | 明确配件/耗材/软件/安装件，不应出图 |
| 视频(V3) | 96 | 视频设备（V3 后期） |
| 电源/PDU(非信号) | 44 | 电源/PDU（非信号链） |
| 通讯(V2) | 28 | 内部通讯设备（V2 扩充） |
| 中控(V2) | 26 | 中控设备（V2 扩充） |
| 灯光(V3) | 2 | 灯光设备（V3 后期） |

## 4. 各品牌覆盖（命中率 Top 15）

| 品牌 | 总数 | 音频命中 | 命中率 | 需人工 |
| --- | ---: | ---: | ---: | ---: |
| EAW | 316 | 271 | 86% | 45 |
| IPS | 285 | 158 | 55% | 127 |
| AUDIX | 235 | 157 | 67% | 78 |
| ezacoustics | 219 | 164 | 75% | 55 |
| ALLEN&HEATH | 128 | 109 | 85% | 19 |
| Televic | 127 | 80 | 63% | 47 |
| Apart | 117 | 102 | 87% | 15 |
| Powersoft | 97 | 82 | 85% | 15 |
| Community | 89 | 78 | 88% | 11 |
| Symetrix | 75 | 35 | 47% | 40 |
| Visionary | 55 | 7 | 13% | 48 |
| Pan Acoustics | 49 | 45 | 92% | 4 |
| Mackie | 47 | 41 | 87% | 6 |
| YAMAHA | 46 | 29 | 63% | 17 |
| (空) | 43 | 27 | 63% | 16 |

## 5. 需人工处理清单

后置/未识别共 **769** 条，分两类：

- **音频范围内待补参数：573 条**（`音频未识别` + `音频配件`）—— 已被收录但分类/参数缺失，需人工确认类别或回填参数后可出图。
- **超出 V1 范围（已正确分类，按规划后置）：196 条**（`视频/中控/灯光/通讯/电源`）—— V1 音频不做，V2/V3 阶段纳入，无需补参数。

### 5a. 音频待补参数清单（前 30 条，完整见 CSV）

| 品牌 | 型号 | 名称 | 后置原因 |
| --- | --- | --- | --- |
|  | AB1608-RK19X | AB1608/DX168机架套件(国产) | 音频未识别 |
|  | CC-STN | CC-7/10的桌面支架 | 音频未识别 |
|  | CC-BRK | CC-7/10的墙面/玻璃安装支架 | 音频未识别 |
|  | DT02/X | 48kHz/96kHz 双通道 Dante输入转模拟输出转换盒 | 音频未识别 |
|  | DT20/X | 48kHz/96kHz 双通道模拟输入转Dante输出转换盒 | 音频未识别 |
|  | DT-SMK | 用于标准版DT20/DT02的平面安装耳朵 | 音频未识别 |
|  | AVANT-BRKT/X | Avantis iPad 支架 | 音频未识别 |
| ALLEN&HEATH | IP1-BK-EU | 小型控制器 | 音频未识别 |
| ALLEN&HEATH | IP1-WH-EU | 小型控制器 | 音频未识别 |
| ALLEN&HEATH | IP1-BK-US | 小型控制器 | 音频未识别 |
| ALLEN&HEATH | IP1-WH-US | 小型控制器 | 音频未识别 |
| ALLEN&HEATH | IP1-WH-EU/X | 小型控制器 | 音频未识别 |
| ALLEN&HEATH | MPS-16 | 电源模块 | 音频未识别 |
| ALLEN&HEATH | ER6N5B6SM100UAV | Link Srl 品牌六类网线缆轴 | 音频未识别 |
| ALLEN&HEATH | SQ-BRACKETX | SQ 可拆装金属支架 | 音频未识别 |
| ATTEROTECH |  | Dante网络音频监听机 | 音频未识别 |
| ATTEROTECH |  | 寻呼台 | 音频未识别 |
| ATTEROTECH |  | 寻呼台 | 音频未识别 |
| AUDIX | TM2SP | 入耳式耳机测量声学耦合器 | 音频未识别 |
| AUDIX | A10 | 入耳式耳机 | 音频未识别 |
| AUDIX | A140 | 耳机 | 音频未识别 |
| AUDIX | A150 | 耳机 | 音频未识别 |
| AUDIX | PLENHSEM3 | 网线接头黄铜压力保护罩 | 音频未识别 |
| AUDIX | PLENHSEM3 | 网线接头黄铜压力保护罩 | 音频未识别 |
| Apart | MASK2CMT-W | 支柱安装支架套件 | 音频未识别 |
| Apart | N-VOLST-W | 立体声音量控制器 | 音频未识别 |
| Apart | E-VOL20 White | 定压单声道墙面音量控制器 | 音频未识别 |
| Apart | E-VOL40 WHITE | 定压单声道墙面音量控制器 | 音频未识别 |
| Apart | E-VOL60 WHITE | 定压单声道墙面音量控制器 | 音频未识别 |
| Apart | E-VOL120 | 定压单声道墙面音量控制器 | 音频未识别 |

## 6. 复用方式

```bash
# 1) 重新解析型号库（公司模板更新后重跑）
python scripts/build_catalog.py "/path/新清单.xlsx"

# 2) 在出图流程中按 品牌+型号+数量 自动匹配（确定性，无 LLM）
from avcad.parse.product_resolver import enrich
bom = [{'brand':'Powersoft','model':'Ottocanali 4K4','quantity':2}, ...]
enrich(bom)   # 就地补全 category/features/params
```

完整清单：`deliverables/catalog_report_deferred.csv`（769 条，含「类型/处理建议」列）