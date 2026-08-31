/**
 * 端到端：图例端口「信号下拉」不得静默改写端口信号
 *
 * 背景（2026-08-31）：`RS232` 在后端枚举、配色表里都齐全，唯独漏在前端
 * `SIGNALS` 常量里。渲染下拉的 sel() 只做 `o === val` 匹配、没有兜底：
 *
 *     const sel = (arr, val)=>arr.map(o=>'<option' + (o === val ? " selected" : "") ...
 *
 * 值不在候选里时**没有任何 option 被选中**，而 `<select>` 默认选中第一项（XLR）。
 * 用户哪怕只改了端口标签，sync() 就把 select.value 写回 p.signal，RS232 静默
 * 变成 XLR。图例库里 4 个设备（EM20D / EM30D / EM50Q / GMN1208D）的 RS232 端口
 * 都处在危险中。
 *
 * 本脚本验证两件事：
 *   1. RS232 端口在下拉里能被正确选中，编辑并保存后服务端仍是 RS232
 *   2. 兜底生效：即使遇到常量里没有的信号（MIDI），也不被改写成首项 XLR
 *
 * 用法: node scripts/e2e_signal_dropdown.js   （需先起服务，默认 :8900）
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8900";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");

const BOM = "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n" +
            "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n" +
            "SOURCE,,,,会议话筒,4,,,,\n";

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

const cardIndex = (d, model) =>
  [...d.querySelectorAll("#legends .legend")]
    .findIndex(c => (c.querySelector(".th b") || {}).textContent === model);

/** 读某张卡片的端口行：下拉里的信号值 + 标签 */
function readSigs(d, i) {
  return [...d.querySelectorAll(`#lgrows_${i} .prow`)].map(r => ({
    signal: (r.querySelector("select.sg") || {}).value,
    label: (r.querySelector('input[type=text]') || {}).value,
  }));
}

(async () => {
  console.log("【会话】解析 → 第③步注入 RS232 端口 → 改标签 → 保存 → 复查服务端");
  const dom = await boot();
  const w = dom.window, d = w.document;
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  $("bom").value = BOM;
  click($("btnParse"));
  await sleep(1500);
  ok(!$("panel2").classList.contains("hidden"), "已解析，进入第 2 步");
  click($("btnToStep3"));
  await sleep(1500);

  const i = cardIndex(d, "TF5");
  ok(i >= 0, `找到 TF5 图例卡片（索引 ${i}）`);

  /* ---------- 1. RS232 能被正确选中 ---------- */
  // MIDI 是**故意**构造的、常量里没有的信号，用来验证 sel() 的兜底分支
  $("lgjson_" + i).value = JSON.stringify([
    { signal: "RS232", role: "io", side: "left",  count: 1, label: "RS232", air: false },
    { signal: "MIDI",  role: "io", side: "right", count: 1, label: "MIDI",  air: false },
  ]);
  $("lgjson_" + i).dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(400);

  let rows = readSigs(d, i);
  console.log("     渲染结果:", JSON.stringify(rows));

  ok(rows[0] && rows[0].signal === "RS232",
     `RS232 端口下拉选中值 = ${rows[0] && rows[0].signal}（应为 RS232）` +
     (rows[0] && rows[0].signal !== "RS232" ? "  ← 被静默改写了！" : ""));

  /* ---------- 2. 兜底：未知信号也不被改写成首项 ---------- */
  ok(rows[1] && rows[1].signal === "MIDI",
     `未知信号 MIDI 的下拉选中值 = ${rows[1] && rows[1].signal}`
     + `（应为 MIDI，验证兜底分支把原值补进了选项）`
     + (rows[1] && rows[1].signal !== "MIDI"
       ? `  ← 兜底失效，被改写成首项 ${rows[1] && rows[1].signal}` : ""));

  /* ---------- 3. 编辑并保存后，服务端仍是 RS232 ---------- */
  // 把 MIDI 那行换成 RS232，并改标签——模拟阳哥「只改标签却把信号弄丢」的操作
  $("lgjson_" + i).value = JSON.stringify([
    { signal: "RS232", role: "io", side: "left",  count: 1, label: "RS232-A", air: false },
    { signal: "RS232", role: "io", side: "right", count: 1, label: "RS232-B", air: false },
  ]);
  $("lgjson_" + i).dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(400);

  // 在界面上只改第一个端口的标签（不动下拉），验证信号不会被带歪
  const labelInput = d.querySelector(`#lgrows_${i} .prow input[type=text]`);
  labelInput.value = "RS232-A改";
  labelInput.dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(200);

  rows = readSigs(d, i);
  console.log("     改标签后:", JSON.stringify(rows));
  ok(rows[0] && rows[0].signal === "RS232",
     `只改标签后信号仍为 ${rows[0] && rows[0].signal}（应为 RS232）`);

  click($("lgok_" + i));
  await sleep(900);
  ok(($("lgst_" + i).textContent || "").includes("已保存"),
     "保存成功：" + $("lgst_" + i).textContent);

  const saved = await (await fetch(BASE + "/api/legend", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "list" }),
  })).json();
  const rec = (saved.legends || []).find(l => l.model === "TF5");
  ok(!!rec, "服务端缓存中已有 TF5 记录");
  const sigs = (rec && rec.ports || []).map(p => p.signal);
  console.log("     服务端端口信号:", JSON.stringify(sigs));
  ok(sigs.length === 2 && sigs.every(s => s === "RS232"),
     "服务端落盘的 2 个端口信号均为 RS232（未被改写成 XLR）");
  ok((rec.ports || []).some(p => p.label === "RS232-A改"),
     "标签改动已一起落盘");

  console.log("\nRESULT: " + (fails ? `FAIL ❌ (${fails})` : "PASS ✅"));
  dom.window.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
