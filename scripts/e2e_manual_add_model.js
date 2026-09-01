/**
 * 端到端：图例校正页「+ 手工添加型号」
 *
 * 阳哥 R12 反馈的根因场景：配单里出现主库没有的新型号时，
 *   ① 第②步看不见它（_resolved 从不进 UI）
 *   ② 图例校正页也搜不到它（列表 = 图例库 ∪ 主库，两边都没有）
 *   → 唯一的补救口是等它出现在第③步，但那要靠运气。
 *
 * 本脚本守「手工建档」这条最后入口：
 *   1. 点 + 能插出一张空白卡，默认类别是 **IO**（引擎对未知型号的兜底类别）
 *   2. 品牌/型号是输入框，能同步回内存
 *   3. 型号为空 / 零端口时保存被拦下，且不写盘
 *   4. 填全后保存 → 服务端图例库真的多出这一条
 *   5. ★ DIRTY 索引平移：先改脏第 2 张卡，再点 +，「待保存」必须还在原来
 *      那台设备上，不能错位到别的卡上
 *
 * ⚠ 会真写 avcad/data/legend_library.json（并可能触发 R10 反推主库）。
 *   脚本自己会备份 + 进程退出时还原（scripts/e2e_lib.js），不留脏记录。
 *
 * 用法: node scripts/e2e_manual_add_model.js
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const { protectLegendLibrary, legendList } = require("./e2e_lib");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8766";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "catalog.html");

const BRAND = "E2ETest";
const MODEL = "E2E-NOCAT-9999";

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function boot() {
  const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), {
    url: BASE + "/catalog", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = () => {};
      // JSDOM 没实现 scrollIntoView，btnAdd 里会调 —— 不桩会抛
      w.Element.prototype.scrollIntoView = function () {};
      w.fetch = (u, o) => globalThis.fetch(BASE + u, o);
    },
  });
  await new Promise(r => dom.window.addEventListener("load", r));
  await sleep(900);          // 等 loadMeta + loadItems 两次请求回来
  return dom;
}

const txt = (d, id) => ((d.getElementById(id) || {}).textContent || "").trim();

async function serverHas(brand, model) {
  const all = await legendList();
  return all.find(l => l.brand === brand && l.model === model) || null;
}

(async () => {
  protectLegendLibrary();
  console.log("【图例校正页 · 手工添加型号】base = " + BASE);
  const dom = await boot();
  const w = dom.window, d = w.document;
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const setVal = (el, v) => { el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); };

  ok(!!$("btnAdd"), "工具栏有「+ 手工添加型号」按钮");
  const before = d.querySelectorAll("#list .legend").length;
  console.log("     当前列表卡片数:", before);

  /* ---------- ① 插入空白卡 ---------- */
  console.log("\n① 点「+ 手工添加型号」");
  click($("btnAdd"));
  await sleep(200);
  const cards = d.querySelectorAll("#list .legend").length;
  ok(cards === before + 1, `列表 +1 张卡（${before} → ${cards}）`);
  ok(!!$("model_input_0"), "新卡在第 0 位，型号是输入框");
  ok(!!$("brand_input_0"), "新卡在第 0 位，品牌是输入框");
  const defCat = ($("cat_sel_0") || {}).value;
  ok(defCat === "IO",
     `默认类别 = ${defCat}（应为 IO —— 引擎对未知型号的兜底类别，SOURCE 是错的）`);
  ok(/未建档/.test(txt(d, "bdg_0")), "徽章提示未建档：" + txt(d, "bdg_0"));

  /* ---------- ② 空型号 / 零端口要被拦下 ---------- */
  console.log("\n② 校验：型号为空 → 拦下，不写盘");
  const n0 = (await legendList()).length;
  click($("addport_0"));                       // 先加端口，排除「至少加一个端口」这条
  await sleep(120);
  click($("ok_0"));
  await sleep(600);
  ok(/型号/.test(txt(d, "st_0")), "提示填写型号：" + txt(d, "st_0"));
  const n1 = (await legendList()).length;
  ok(n1 === n0, `图例库条数未变（${n0} → ${n1}）—— 空型号不能落盘`);

  /* ---------- ③ 填全并保存 ---------- */
  console.log("\n③ 填品牌 / 型号 → 保存落图例库");
  setVal($("brand_input_0"), BRAND);
  setVal($("model_input_0"), MODEL);
  await sleep(150);
  const row0 = d.querySelector("#port_rows_0 .prow");
  ok(!!row0, "已有一个端口行");
  setVal(row0.querySelector(".lb"), "IN");      // 标签
  click(row0.querySelectorAll("button.mini")[1]);   // ＋ 数量 → 2
  await sleep(120);
  const cnt = (d.querySelector("#port_rows_0 .prow span.n") || {}).textContent;
  ok(cnt === "2", `端口数点 ＋ 后 = ${cnt}（应为 2）`);

  click($("ok_0"));
  await sleep(1200);
  console.log("     保存状态:", txt(d, "st_0"));
  ok(/已落盘/.test(txt(d, "st_0")), "界面显示已落盘");
  ok(/已落盘/.test(txt(d, "bdg_0")), "徽章更新为已落盘：" + txt(d, "bdg_0"));

  const rec = await serverHas(BRAND, MODEL);
  ok(!!rec, "★ 服务端图例库里已有 " + BRAND + "/" + MODEL);
  if (rec) {
    console.log("     服务端记录:", JSON.stringify(rec.ports));
    const p = (rec.ports || [])[0] || {};
    ok(p.label === "IN" && p.count === 2,
       `端口落盘正确：label=${p.label} count=${p.count}（应 IN / 2）`);
    ok(rec.category === "IO", `类别落盘 = ${rec.category}（应 IO）`);
  }

  /* ---------- ④ DIRTY 索引平移 ---------- */
  console.log("\n④ DIRTY 平移：改脏第 2 张卡 → 再点 + → 待保存仍在第 3 张");
  const target = d.querySelectorAll("#list .legend")[1];
  const targetTitle = (target.querySelector(".th .left b") || {}).textContent || "";
  // 用「改类别」触发 mark()，等价于用户动了这张卡
  const sel = target.querySelector('select[id^="cat_sel_"]');
  ok(!!sel, "找到第 2 张卡的类别下拉");
  const other = [...sel.options].map(o => o.value).find(v => v !== sel.value);
  sel.value = other;
  sel.dispatchEvent(new w.Event("change", { bubbles: true }));   // 触发 mark()
  await sleep(150);
  ok(/待保存/.test((target.querySelector(".cache") || {}).textContent || ""),
     "第 2 张卡已改脏（改类别）：" + ((target.querySelector(".cache") || {}).textContent || "").trim());
  const before2 = d.querySelectorAll("#list .legend").length;
  click($("btnAdd"));
  await sleep(250);
  const after2 = d.querySelectorAll("#list .legend").length;
  ok(after2 === before2 + 1, `卡片数 ${before2} → ${after2}`);
  const moved = d.querySelectorAll("#list .legend")[2];
  const movedTitle = (moved.querySelector(".th .left b") || {}).textContent || "";
  const movedBadge = (moved.querySelector(".cache") || {}).textContent || "";
  const newBadge = (d.querySelectorAll("#list .legend")[0]
                      .querySelector(".cache") || {}).textContent || "";
  ok(movedTitle === targetTitle,
     `原第 2 张卡（${targetTitle}）现在在第 3 位（当前 ${movedTitle}）`);
  ok(/待保存/.test(movedBadge), "「待保存」跟着平移到新索引：" + movedBadge.trim());
  ok(/未建档/.test(newBadge), "新插的卡自己是「未建档」：" + newBadge.trim());

  dom.window.close();

  console.log("\n" + (fails ? `❌ ${fails} 项未通过` : "✅ 全部通过"));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error("崩溃:", e); process.exit(2); });
