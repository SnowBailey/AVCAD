/**
 * 端到端：配单里出现「主库没有的新型号」——第②步看得见 + 第③步初值准
 *
 * 阳哥 R12 提的问题：「配单中出现新型号的话，这个软件会如何处理？」
 * 改动前的实测答案（写探针跑真实管线得出，不是推测）：
 *   · 主库三级匹配 + 内置 MODEL_DB 都不中 → _resolved = "fallback"
 *   · importers 兜底成 IO → chain 扔进 SIDE 层 → **连线数 0，孤立方块**
 *   · 校验层一声不吭（只多 2 条 INFO:UNCONNECTED，混在几十条同类里）
 *   · 而 _resolved 在 avcad/ui/ 里零引用 —— 第②步完全看不出来
 *
 * 本脚本守改完后的行为：
 *   ② 步：unknownBox 列出未收录型号，卡上带「主库未收录」徽章
 *   ③ 步：端口初值来自**引擎规格模板**（IO = 1进1出），不是前端硬编码的 4进4出
 *        已经手工建档过的型号 → 直接套用图例库里的值（IN×2）
 *
 * ⚠ 需要图例库里有一条「手工建档」的 E2ETest / E2E-NOCAT-9999 / IO / IN×2
 *   来验证「下次配单自动套用」。本脚本自己 seed 这条记录（等价于在页面上
 *   手工建档），并在进程退出时还原图例库（scripts/e2e_lib.js），
 *   所以**单独跑、也不用先跑 e2e_manual_add_model.js**。
 *
 * 用法: node scripts/e2e_new_model_visible.js
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const { protectLegendLibrary, seedLegend } = require("./e2e_lib");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8766";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");

/* 四个型号覆盖四种来源：
   TF5            → 主库命中（catalog），**且图例库里有用户保存的值 rev N**
                    → 第③步必须显示用户保存的值，证明「图例库优先于引擎推断」
   MX-8           → 两库都没有，但清单给了类别与参数（inputs=8）
                    → 引擎按参数展开 IN×8，不是前端模板的 4
   E2E-NOCAT-9999 → 主库没有，但图例库里已手工建档（IN×2）
   ZZZ-NEW-0001   → 两库都没有、连类别都没给 → 引擎 IO 模板 IN×1 OUT×1 */
const BOM = "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n" +
            "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n" +
            "MIXER,FooBrand,MX-8,未收录调音台,1,,inputs=8;outputs=4,,,\n" +
            ",E2ETest,E2E-NOCAT-9999,手工建档测试机,1,,,,\n" +
            ",Zzz,ZZZ-NEW-0001,全新未知设备,1,,,,\n" +
            "SPEAKER,L-Acoustics,KARA,主扩扬声器,2,,impedance_ohm=8;power_w=400,,,\n" +
            "AMP,Powersoft,Quattrocanali 4804,功放,1,dante,channels=4,,,\n";

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function boot() {
  const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), {
    url: BASE + "/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = () => {};
      w.Element.prototype.scrollIntoView = function () {};
      w.fetch = (u, o) => globalThis.fetch(BASE + u, o);
    },
  });
  await new Promise(r => dom.window.addEventListener("load", r));
  await sleep(400);
  return dom;
}

/** 找到型号为 model 的图例卡片索引 */
function cardIndex(d, model) {
  return [...d.querySelectorAll("#legends .legend")]
    .findIndex(c => (c.querySelector(".th b") || {}).textContent === model);
}
/** 读某张卡的所有端口行 */
function readRows(d, i) {
  return [...d.querySelectorAll(`#lgrows_${i} .prow`)].map(r => ({
    label: (r.querySelector('input[type=text]') || {}).value,
    count: +((r.querySelector("span.n") || {}).textContent || "0"),
  }));
}

(async () => {
  protectLegendLibrary();
  /* 预置一条「手工建档」的图例：验证它能在第③步被自动套用。
     等价于阳哥在图例校正页用「+ 手工添加型号」建的那条。 */
  await seedLegend({
    brand: "E2ETest", model: "E2E-NOCAT-9999", category: "IO",
    ports: [{ signal: "XLR", role: "in", side: "left", count: 2, label: "IN", air: false }],
  });
  console.log("【新型号可见性】base = " + BASE);
  const dom = await boot();
  const w = dom.window, d = w.document;
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  /* ---------- ② 模块确认：看得见 ---------- */
  console.log("\n① 解析清单 → 第②步");
  $("bom").value = BOM;
  click($("btnParse"));
  await sleep(2200);
  ok(!$("panel2").classList.contains("hidden"), "已解析，进入第 2 步");

  const box = $("unknownBox");
  ok(!!box && !box.classList.contains("hidden"), "⚠ 未收录提示框已显示");
  const notice = (box.textContent || "").replace(/\s+/g, " ");
  console.log("     提示内容:", notice);
  ok(/ZZZ-NEW-0001/.test(notice), "提示里点名了纯新型号 ZZZ-NEW-0001");
  ok(/E2E-NOCAT-9999/.test(notice), "提示里也列出 E2E-NOCAT-9999（图例库有、主库没有）");
  ok(!/TF5/.test(notice), "主库命中的 TF5 不该出现在未收录名单里");

  /* 徽章数应与提示框点名数一致（都来自 STATE.modules 的 source==="unknown"），
     写死数字会随 BOM 改动而误报。 */
  const named = ((box.textContent || "").match(/· /g) || []).length;
  const chips = [...d.querySelectorAll("#modules .chip.unknown")];
  ok(chips.length === named && named === 3,
     `模块卡上有 ${chips.length} 个「主库未收录」徽章，与提示框点名的 ${named} 个一致（应 3）`);
  const chipText = [...d.querySelectorAll("#modules .dev")]
    .filter(c => c.querySelector(".chip.unknown"))
    .map(c => ((c.querySelector(".meta") || {}).textContent || "").trim());
  console.log("     带徽章的模块:", JSON.stringify(chipText));

  /* ---------- ③ 图例确认：初值准 ---------- */
  console.log("\n② 进入第③步图例确认");
  click($("btnToStep3"));
  await sleep(2200);

  const iNew = cardIndex(d, "ZZZ-NEW-0001");
  ok(iNew >= 0, `找到纯新型号的图例卡（索引 ${iNew}）`);
  if (iNew >= 0) {
    const rows = readRows(d, iNew);
    console.log("     ZZZ-NEW-0001 端口初值:", JSON.stringify(rows));
    const total = rows.reduce((s, r) => s + r.count, 0);
    ok(total === 2, `★ 端口总数 = ${total}（引擎 IO 模板 1进1出，应 2；`
                  + `改动前前端硬编码给的是 4进4出 = 8）`);
    const ins = rows.filter(r => /IN/i.test(r.label)).reduce((s, r) => s + r.count, 0);
    ok(ins === 1, `输入口 = ${ins}（IO 规格模板是 1，不是 4）`);
  }

  const iMan = cardIndex(d, "E2E-NOCAT-9999");
  ok(iMan >= 0, `找到手工建档型号的图例卡（索引 ${iMan}）`);
  if (iMan >= 0) {
    const rows = readRows(d, iMan);
    console.log("     E2E-NOCAT-9999 端口初值:", JSON.stringify(rows));
    const inRow = rows.find(r => r.label === "IN");
    ok(!!inRow && inRow.count === 2,
       `★ 手工建档的 IN×2 被自动套用（当前 ${inRow && inRow.count}）`);
    const badge = ($("lgcache_" + iMan).textContent || "").trim();
    ok(/图例库/.test(badge), "徽章显示来自图例库：" + badge);
  }

  const iMx = cardIndex(d, "MX-8");
  ok(iMx >= 0, `找到未收录调音台的图例卡（索引 ${iMx}）`);
  if (iMx >= 0) {
    const rows = readRows(d, iMx);
    console.log("     MX-8 端口初值:", JSON.stringify(rows));
    const inRow = rows.find(r => r.label === "IN");
    ok(!!inRow && inRow.count === 8,
       `★ 未收录但给了参数的调音台：IN = ${inRow && inRow.count}`
       + `（引擎按清单 inputs=8 展开，前端硬编码模板是 4）`);
    ok(/图例/.test(($("lgcache_" + iMx).textContent || "") + "图例")
       && !/图例库/.test($("lgcache_" + iMx).textContent || ""),
       "MX-8 没有已保存图例，徽章不该显示「图例库」："
       + ($("lgcache_" + iMx).textContent || "").trim());
  }

  const iTf5 = cardIndex(d, "TF5");
  ok(iTf5 >= 0, "找到 TF5 图例卡");
  if (iTf5 >= 0) {
    const rows = readRows(d, iTf5);
    const badge = ($("lgcache_" + iTf5).textContent || "").trim();
    console.log("     TF5 端口初值:", JSON.stringify(rows), "| 徽章:", badge);
    /* ★ 优先级不变式：图例库 > 引擎推断。
       TF5 在图例库里有用户自己保存过的值（rev 35 / IN×7），
       跟清单写的 inputs=32 不一样 —— 第③步必须显示用户保存的那个。
       改动前这一步是「前端硬编码 4进4出」，既不是 32 也不是 7。 */
    ok(/图例库/.test(badge), "TF5 徽章显示来自图例库：" + badge);
    const inRow = rows.find(r => r.label === "IN");
    ok(!!inRow && inRow.count !== 4,
       `★ 图例库值优先于引擎推断：IN = ${inRow && inRow.count}`
       + `（既不是前端模板的 4，也不是清单的 32，而是用户保存的 7）`);
  }

  dom.window.close();
  console.log("\n" + (fails ? `❌ ${fails} 项未通过` : "✅ 全部通过"));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error("崩溃:", e); process.exit(2); });
