/**
 * 端到端：走到第 5 步，验证「隐藏品牌型号」开关可见、可点、出图后仍可达。
 * 用法: node scripts/e2e_step5_anon.js  (需先启动 python -m avcad ui --port 8900)
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8900";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");

const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
function ok(cond, msg) {
  console.log((cond ? "  ✅ " : "  ❌ ") + msg);
  if (!cond) fails++;
}

(async () => {
  const html = fs.readFileSync(HTML, "utf8");
  const dom = new JSDOM(html, {
    url: BASE + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => globalThis.fetch(BASE + u, o);
      w.performance = w.performance || { now: () => Date.now() };
      w.scrollTo = () => {};
    },
  });
  const w = dom.window, d = w.document;
  await new Promise(r => w.addEventListener("load", r));
  await sleep(400);

  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  console.log("【步骤1】解析清单");
  click($("btnParse"));
  await sleep(1200);
  ok(!$("panel2").classList.contains("hidden"), "进入第 2 步");
  const devs = d.querySelectorAll("#modules .dev").length;
  ok(devs > 0, `模块列表渲染 ${devs} 项`);

  console.log("【步骤2】模块确认");
  click($("btnToStep3"));
  await sleep(600);
  ok(!$("panel3").classList.contains("hidden"), "进入第 3 步");

  console.log("【步骤3】图例确认（保存全部）");
  await sleep(400);
  click($("btnLegSaveAll"));
  await sleep(1500);
  click($("btnToStep4"));
  await sleep(800);
  ok(!$("panel4").classList.contains("hidden"), "进入第 4 步");

  console.log("【步骤4】架构选择");
  click($("btnToStep5"));
  await sleep(1500);

  console.log("【步骤5】出图前确认 —— 品牌型号开关可见性");
  ok(!$("panel5").classList.contains("hidden"), "第 5 步面板可见");
  ok(!$("genGate").classList.contains("hidden"), "出图前确认区（genGate）可见");
  const gateOpts = d.querySelectorAll("#anonBtns button.opt");
  ok(gateOpts.length === 2, `gate 内 2 个选项按钮（实际 ${gateOpts.length}）`);
  ok(gateOpts[0].textContent.includes("显示真实厂商与型号"), "选项①=显示真实厂商与型号");
  ok(gateOpts[1].textContent.includes("隐藏厂商与型号"), "选项②=隐藏厂商与型号");
  ok($("anonHint").textContent.includes("显示"), "提示文案当前为显示说明");

  console.log("【步骤5】切换到「隐藏」");
  click(gateOpts[1]);
  await sleep(200);
  ok(gateOpts[1].classList.contains("on"), "gate 内「隐藏」被选中");
  ok(w.eval("STATE.anon") === true, "STATE.anon === true");
  ok($("anonHint").textContent.includes("隐藏"), "提示文案已更新为隐藏说明");

  console.log("【步骤5】图例一致性检查");
  const lc = $("legendCheck").textContent;
  ok(lc.length > 0 && lc !== "检查中…", "图例检查已完成：" + lc.slice(0, 40).replace(/\n/g, " "));
  if ($("btnGenGo").disabled) {
    console.log("  ⚠️ 开始出图按钮被禁用（有图例未确认），跳过出图");
  } else {
    console.log("【步骤5】出图");
    click($("btnGenGo"));
    await sleep(3000);
    ok(!$("genDone").classList.contains("hidden"), "出图完成，切到结果区");
    ok($("svg").innerHTML.includes("<svg"), "SVG 已渲染");

    console.log("【步骤5】出图后用 gate 选项切回「显示」→ 应自动重新出图");
    click(gateOpts[0]);
    await sleep(3000);
    ok(w.eval("STATE.anon") === false, "STATE.anon 回到 false");
    ok($("genReport").textContent.includes("显示真实厂商与型号"), "出图说明记录了品牌型号处理方式");
    ok($("genReport").textContent.includes("源模块右侧出线段"), "出图说明记录了线标规则");

    console.log("【步骤5】用「修改出图设置」回到 gate");
    click($("btnEditGate"));
    await sleep(300);
    ok(!$("genGate").classList.contains("hidden"), "重新显示出图前确认区");
    ok($("genDone").classList.contains("hidden"), "结果区已隐藏");
  }

  console.log("\nRESULT: " + (fails ? `FAIL ❌ (${fails})` : "PASS ✅"));
  dom.window.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
