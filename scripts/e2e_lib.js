/**
 * e2e 公共工具：图例库「借了就还」
 *
 * 这些脚本跑的是**真实服务**（默认 http://127.0.0.1:8766），保存图例
 * 会真的写 avcad/data/legend_library.json —— 那是 30 条阳哥一条条确认过的
 * 永久文档。忘了还原，一次跑测试就在 git status 里留下一条脏记录。
 *
 * 用法：
 *   const { protectLegendLibrary, seedLegend } = require("./e2e_lib");
 *   const restore = protectLegendLibrary();   // 首次调用时备份，进程退出时还原
 *   await seedLegend({ brand, model, category, ports });  // 直接写服务端
 *   ...断言...
 *   restore();                                 // 也可以提前手动还原
 */
const fs = require("fs");
const path = require("path");

const LIB = path.join(__dirname, "..", "avcad", "data", "legend_library.json");
const BAK = path.join(require("os").tmpdir(), "avcad-e2e-legend-backup.json");

const BASE = () => process.env.AVCAD_BASE || "http://127.0.0.1:8766";

/** 备份图例库，并注册进程退出时的自动还原。返回手动还原函数。 */
function protectLegendLibrary() {
  if (!fs.existsSync(LIB)) {
    console.log("  ⚠ 找不到图例库 " + LIB + "，跳过保护");
    return () => {};
  }
  if (!fs.existsSync(BAK)) fs.copyFileSync(LIB, BAK);
  let done = false;
  const restore = () => {
    if (done || !fs.existsSync(BAK)) return;
    fs.copyFileSync(BAK, LIB);
    done = true;
    console.log("\n  ♻ 图例库已还原（测试期间写入的记录已清除）");
  };
  process.on("exit", restore);
  process.on("SIGINT", () => { restore(); process.exit(130); });
  process.on("uncaughtException", e => { restore(); console.error(e); process.exit(2); });
  return restore;
}

/** 往服务端图例库写一条（经 /api/legend，等价于在页面上点保存） */
async function seedLegend({ brand, model, category, ports }) {
  const r = await fetch(BASE() + "/api/legend", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brand, model, category, ports, slots: [], note: "" }),
  });
  const j = await r.json();
  if (j.error) throw new Error("seedLegend 失败：" + j.error);
  return j;
}

/** 服务端图例库快照 */
async function legendList() {
  const r = await fetch(BASE() + "/api/legend", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "list" }),
  });
  return (await r.json()).legends || [];
}

module.exports = { protectLegendLibrary, seedLegend, legendList, LIB, BAK };
