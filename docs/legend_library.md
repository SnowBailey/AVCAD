# 图例库（永久文档）数据结构说明

> 一句话：**这不是缓存，是一份可长期维护的永久文档。**
> 每次在第 ③ 步确认 / 修改图例 → **立即原子写盘** → revision 递增 + 保留历史。
> 下次遇到相同「品牌 + 型号 + 类别」，读到的就是**最后一次维护**的结果。
> **图例库优先级 > 引擎推断值。**

---

## 1. 文件位置

| 文件 | 用途 |
|---|---|
| `avcad/data/legend_library.json` | **永久图例库**（唯一写入目标） |
| `avcad/data/legend_cache.json` | 旧文件，仅在库文件不存在时**迁移一次**，此后不再写入 |

写入方式：先写 `*.tmp` 再 `os.replace` 原子替换，断电/崩溃不会产生半截文件。

---

## 2. 整体结构

```jsonc
{
  "schema": "avcad.legend-library/1",
  "kind": "永久图例库（非缓存；图例库优先级高于引擎推断）",
  "updated_at": "2026-08-30T02:19:58+08:00",
  "count": 23,
  "legends": [ /* 见下 */ ]
}
```

| 字段 | 说明 |
|---|---|
| `schema` | 结构版本号，便于以后升级时做兼容迁移 |
| `kind` | 人类可读的定位声明（强调「非缓存」与优先级） |
| `updated_at` | 整个库最后一次写入时间 |
| `count` | 图例条数 |
| `legends` | 图例条目数组 |

---

## 3. 单条图例

```jsonc
{
  "brand": "Yamaha",
  "model": "TF5",
  "category": "MIXER",
  "key": "Yamaha::TF5::MIXER",      // 由前三列推导，冗余存一份便于人工查看

  "ports": [                        // 端口「类」列表（一类 N 口）
    { "signal": "XLR",   "role": "in",  "side": "left",  "count": 7, "label": "IN",    "air": false },
    { "signal": "XLR",   "role": "out", "side": "right", "count": 3, "label": "OUT",   "air": false },
    { "signal": "DANTE", "role": "io",  "side": "right", "count": 1, "label": "DANTE", "air": false }
  ],
  "slots": [],                      // 卡槽条可视化，如 [{type,count,label}]
  "note": "",

  "source": "user",                 // user=用户确认 / engine=引擎回填 / migrated=迁移
  "revision": 3,                    // 第几次维护
  "created_at": "2026-08-30T02:15:18+08:00",
  "updated_at": "2026-08-30T02:19:58+08:00",
  "history": [ /* 最近 5 次维护的快照 */ ]
}
```

### 3.1 端口「类」vs「实例」

这是最容易踩坑的地方，两个来源格式不同：

| 来源 | 格式 | 示例 |
|---|---|---|
| **图例库**（本文件） | 类 + 数量 | `{label:"IN", count:7}` |
| `/api/run` 传回前端 | 展开后的实例，无 `count` | `IN1 … IN7` |

图上最终画的是**实例**：`IN` × 7 → `IN1…IN7`。
前端 `aggregatePorts()` 必须尊重条目自带的 `count`，否则 `IN×7` 会被压成 `IN×1`。

### 3.2 端口字段

| 字段 | 取值 | 说明 |
|---|---|---|
| `signal` | XLR / AES / DANTE / RS232 / IP / GPIO / RF / SPEAKER / POWER / OPTICAL | 信号类型，决定线色与线型 |
| `role` | in / out / io | 端口角色 |
| `side` | left / right / top / bottom | 端口画在模块哪一侧 |
| `count` | ≥1 | 该类的端口数量 |
| `label` | 如 `IN` | 基础名；`count>1` 时展开为 `IN1…INn` |
| `air` | true/false | 空中/非线缆接口（如天线 RF） |

### 3.3 history（维护留痕）

每次维护把**上一版**压入 `history`，最多保留最近 **5** 条：

```jsonc
"history": [
  { "revision": 1, "updated_at": "...", "ports": [...], "slots": [], "note": "", "source": "user" },
  { "revision": 2, "updated_at": "...", "ports": [...], "slots": [], "note": "", "source": "user" }
]
```

用途：改错了可以回看上一版是什么样；也作为「每次维护的永久留档」。

---

## 4. 键：`brand::model::category`

```
Yamaha::TF5::MIXER
_generic::_::SOURCE          ← 无品牌无型号的设备
```

- 空 brand → `_generic`，空 model → `_`，category 为空则退化为两段。
- **`category` 必须参与建键**。否则「会议话筒 / 无线话筒发射端 / 天线 / 扬声器」这些**无品牌无型号**的设备会全部撞到 `_generic::_` 上，保存其中一个就会覆盖掉另外四个（**已修复**）。

---

## 5. 优先级：图例库 > 引擎推断

调用链：

```
build_project(entries, legend_store=...)
   ├─ build_instances(entries)        ← 引擎按规格库推断端口
   └─ for inst: legend_store.apply(inst)   ← 用图例库整体覆盖 inst.ports
```

`apply()` 里是直接 `inst.ports = ports`（**覆盖**，不是合并），所以：

- 库里有这条记录 → **以库为准**，引擎推断被丢弃；
- 库里没有 → 保留引擎推断值兜底。

回填时还会在实例上打标记，便于追查来源：

| 属性 | 说明 |
|---|---|
| `inst.legend_source` | `user` / `engine` / `migrated` |
| `inst.legend_revision` | 用的是第几次维护的版本 |
| `inst.legend_updated_at` | 该版本的维护时间 |

导出（`DXF`）与预览走的是同一条路径，因此**导出的图纸与预览完全一致**。

---

## 6. 落盘时机

| 操作 | 行为 |
|---|---|
| 单卡「确认图例（保存）」 | 立刻落盘 |
| 「全部确认并保存」 | 批量落盘（覆盖「改动过的 ∪ 尚未落盘的」） |
| 第 ⑤ 步「按当前定义一键确认剩余图例」 | 批量落盘后重新校验 |
| **点「下一步：架构选择」离开第 ③ 步** | **自动落盘未保存的改动**，避免改动丢失 |

---

## 7. 本次同步修掉的两个真 bug

1. **`put()` 建键漏了 category** —— 同 brand+model 不同类别互相覆盖。
   实测库里 `("","")` 这一组有 **5 个不同类别**（SOURCE / WIRELESS_MIC / ANTENNA / SPEAKER / ANT_DIST）共用 `_generic::_`，保存任一即丢失其余 4 个。
2. **`put()` 用两段键、载入用三段键** —— 同一条图例在文件里被写成两份，产生重复记录（Yamaha TF5 出现 2 条 MIXER）。

修复后：23 条（去重后）、5 个无品牌设备各自独立。
