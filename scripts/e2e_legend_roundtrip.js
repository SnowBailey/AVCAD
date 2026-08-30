/**
 * 端到端：图例「改 → 存 → 重新导入」往返
 *
 * 阳哥反馈：改过并保存的图例，重新导入清单进入第③步时，
 *          应该展示最后一次修改的值，而不是被压成 1。
 *
 * 会话①：解析清单 → 第③步把 TF5 的 IN 改成 7 → 保存落盘
 * 会话②：全新页面 → 重新解析同一份清单 → 第③步应显示 IN×7
 *
 * 用法: node scripts/e2e_legend_roundtrip.js
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8900";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");

const BOM = "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n" +
            "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n" +
            "SOURCE,,,,会议话筒,4,,,,\n" +
            "SPEAKER,L-Acoustics,KARA,主扩扬声器,2,,impedance_ohm=8;power_w=400,,,\n";

const WANT = 7;   // 我们把 TF5 的 IN 改成 7

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function boot() {
  const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), {
    url: BASE + "/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = () => {};
      w.performance = w.performance || { now: () => Date.now() };
      w.fetch = (u, o) => globalThis.fetch(BASE + u, o);
    },
  });
  await new Promise(r => dom.window.addEventListener("load", r));
  await sleep(400);
  return dom;
}

/** 找到型号为 model 的图例卡片索引 */
function cardIndex(d, model) {
  const cards = [...d.querySelectorAll("#legends .legend")];
  return cards.findIndex(c => (c.querySelector(".th b") || {}).textContent === model);
}
/** 读取某张卡片的所有端口行（标签 / 数量） */
function readRows(d, i) {
  return [...d.querySelectorAll(`#lgrows_${i} .prow`)].map(r => ({
    label: (r.querySelector('input[type=text]') || {}).value,
    count: +((r.querySelector("span.n") || {}).textContent || "0"),
  }));
}

(async () => {
  /* ---------- 会话①：改并保存 ---------- */
  console.log("【会话①】解析 → 第③步把 TF5 的 IN 改成 " + WANT + " → 保存");
  let dom = await boot();
  let w = dom.window, d = w.document;
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  $("bom").value = BOM;
  click($("btnParse"));
  await sleep(1500);
  ok(!$("panel2").classList.contains("hidden"), "已解析，进入第 2 步");
  click($("btnToStep3"));
  await sleep(1500);

  const i1 = cardIndex(d, "TF5");
  ok(i1 >= 0, `找到 TF5 图例卡片（索引 ${i1}）`);
  console.log("     修改前:", JSON.stringify(readRows(d, i1)));

  // 用 JSON 框直接写入想要的定义（等价于在界面上点 ± 改数量）
  const ta = $("lgjson_" + i1);
  ta.value = JSON.stringify([
    { signal: "XLR", role: "in", side: "left", count: WANT, label: "IN", air: false },
    { signal: "XLR", role: "out", side: "right", count: 3, label: "OUT", air: false },
    { signal: "DANTE", role: "io", side: "right", count: 1, label: "DANTE", air: false },
  ]);
  ta.dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(300);
  console.log("     改后界面:", JSON.stringify(readRows(d, i1)));
  click($("lgok_" + i1));
  await sleep(900);
  ok(($("lgst_" + i1).textContent || "").includes("已保存"), "保存成功：" + $("lgst_" + i1).textContent);

  // 直接查服务端确认落盘内容
  const saved = await (await fetch(BASE + "/api/legend", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "list" }),
  })).json();
  const rec = (saved.legends || []).find(l => l.model === "TF5");
  ok(!!rec, "服务端缓存中已有 TF5 记录");
  const inRec = (rec.ports || []).find(p => p.label === "IN");
  ok(inRec && inRec.count === WANT, `服务端记录的 IN count = ${inRec && inRec.count}（应为 ${WANT}）`);
  dom.window.close();

  /* ---------- 会话②：全新页面重新导入 ---------- */
  console.log("\n【会话②】全新页面 → 重新导入同一份清单 → 第③步");
  dom = await boot();
  w = dom.window; d = w.document;
  const $2 = id => d.getElementById(id);
  const click2 = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  $2("bom").value = BOM;
  click2($2("btnParse"));
  await sleep(1500);
  click2($2("btnToStep3"));
  await sleep(1800);

  const i2 = cardIndex(d, "TF5");
  ok(i2 >= 0, `重新导入后找到 TF5 卡片（索引 ${i2}）`);
  const rows = readRows(d, i2);
  console.log("     重新导入后界面显示:", JSON.stringify(rows));
  const inRow = rows.find(r => r.label === "IN");
  ok(!!inRow, "显示中包含 IN 这一类");
  ok(inRow && inRow.count === WANT,
     `IN 的数量 = ${inRow && inRow.count}（应为 ${WANT}）` + (inRow && inRow.count !== WANT ? "  ← 这就是阳哥反馈的 bug" : ""));

  const total = rows.reduce((s, r) => s + r.count, 0);
  ok(total === WANT + 3 + 1, `端口总数 = ${total}（应为 ${WANT + 3 + 1}）`);

  /* 永久图例库的可见证据 */
  const badge = ($2("lgcache_" + i2).textContent || "").trim();
  ok(badge.startsWith("● 图例库"), "徽标显示为图例库条目：" + badge);
  // 维护次数会持续累加（可能到两位数），用数值判断而非 [2-9] 单字符匹配
  const vm = badge.match(/v(\d+)/);
  ok(!!vm && parseInt(vm[1], 10) >= 2, `徽标带维护版本号（第 2 次维护以上）：${badge}`);
  const meta = ($2("lgmeta_" + i2).textContent || "");
  ok(/第 \d+ 次维护/.test(meta), "显示维护次数：" + meta.replace(/\s+/g, " "));
  const libInfo = ($2("legendLibInfo").textContent || "");
  ok(libInfo.includes("legend_library.json"), "图例库路径可见：" + libInfo.replace(/\s+/g, " ").trim());

  /* 服务端确认 revision 已递增 + 历史已留痕 */
  const saved2 = await (await fetch(BASE + "/api/legend", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "list" }),
  })).json();
  const rec2 = (saved2.legends || []).find(l => l.model === "TF5");
  ok(rec2 && rec2.revision >= 2, `服务端 revision = ${rec2 && rec2.revision}（应 ≥ 2）`);
  ok(rec2 && Array.isArray(rec2.history) && rec2.history.length >= 1,
     `保留了 ${rec2 && rec2.history.length} 条历史版本`);
  ok(saved2.library && saved2.library.path.endsWith("legend_library.json"),
     "库文件路径 = " + (saved2.library || {}).path);

  console.log("\nRESULT: " + (fails ? `FAIL ❌ (${fails})` : "PASS ✅"));
  dom.window.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
