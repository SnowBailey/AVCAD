/**
 * 端到端：导入清单(xlsx) / 解析清单(CSV) 都能进入第②步；解析异常时前端给可见提示、不静默卡死。
 *
 * 阳哥 R17 报告：「导入清单和解析清单现在无法跳转第二步」。
 * 根因面：前端 api() 不检查 HTTP 状态码、doParse/uploadFile 的 await 异常无 try/catch
 *         —— 任何真实 BOM 触发后端 500，前端就无声卡在第一步。
 * 本脚本守三件事：
 *   ① CSV 路（解析清单按钮）→ 进入第②步
 *   ② xlsx 路（导入清单，等价于 uploadFile 调 /api/parse）→ 进入第②步
 *   ③ 损坏 xlsx（后端 500 + error）→ 前端 toast 可见提示、不崩溃、仍停第①步
 *
 * 用法: AVCAD_BASE=http://127.0.0.1:8766 node scripts/e2e_parse_step2.js
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8766";
const HTML = "/Users/mac/WorkBuddy/2026-08-28-09-05-56/avcad/avcad/ui/static/index.html";
const XLSX = "/Users/mac/WorkBuddy/2026-08-28-09-05-56/avcad/avcad/samples/sample_bom.xlsx";

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const hardTimeout = setTimeout(() => { console.error("❌ 整体超时（30s）"); process.exit(3); }, 30000);

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

(async () => {
  console.log("【解析→第②步】base = " + BASE);
  const dom = await boot();
  const w = dom.window, d = w.document;
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  // ① CSV 路
  console.log("\n① 解析清单（CSV）→ 第②步");
  const CSV = "设备类型,品牌,型号,名称,数量,特性,参数,冗余,处理器功能,有源\n" +
              "MIXER,Yamaha,TF5,数字调音台,1,dante;control,inputs=32;outputs=16,,,\n" +
              "PROCESSOR,,Xilica,音频处理器,1,dante,inputs=8;outputs=8,,,\n";
  $("bom").value = CSV;
  click($("btnParse"));
  await sleep(1500);
  ok(!$("panel2").classList.contains("hidden"), "CSV 路进入第 2 步");

  // 回到第1步
  click(d.querySelector('#stepper .step[data-n="1"]'));
  await sleep(200);

  // ② xlsx 路（直接走后端解析，等价于 uploadFile 的 api 调用 + parseRes）
  console.log("\n② 导入清单（xlsx）→ 第②步");
  const b64 = fs.readFileSync(XLSX).toString("base64");
  const res = await w.api("/api/parse", {b64, filename: "sample_bom.xlsx"});
  w.parseRes(res);
  await sleep(400);
  ok(!$("panel2").classList.contains("hidden"), "xlsx 路进入第 2 步");
  ok((res.modules || []).length > 0, "xlsx 路返回了模块：" + (res.modules || []).length);

  // 回到第1步
  click(d.querySelector('#stepper .step[data-n="1"]'));
  await sleep(200);

  // ③ 损坏 xlsx → 前端不崩溃、给可见提示
  console.log("\n③ 损坏 xlsx → 前端可见提示且不崩溃");
  const bad = Buffer.from("this is not a zip file").toString("base64");
  let resBad;
  try { resBad = await w.api("/api/parse", {b64: bad, filename: "broken.xlsx"}); }
  catch (e) { resBad = {error: String(e)}; }
  w.parseRes(resBad);
  await sleep(400);
  ok(!!resBad.error, "损坏 xlsx 后端/前端捕获 error：" + String(resBad.error || "").slice(0, 60));
  ok($("panel2").classList.contains("hidden"), "损坏时仍停在第 1 步（无模块可展示）");
  const toastTxt = ($("toast").textContent || "").trim();
  ok(/解析失败|解析异常/.test(toastTxt), "前端 toast 可见提示：" + toastTxt.slice(0, 60));

  dom.window.close();
  clearTimeout(hardTimeout);
  console.log("\n" + (fails ? `❌ ${fails} 项未通过` : "✅ 全部通过"));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error("崩溃:", e); process.exit(2); });
