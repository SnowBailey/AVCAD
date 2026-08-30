/**
 * 第③步图例预览「实时更新」回归测试。
 *
 * 阳哥反馈：改端口参数（数量 / 标签 / 增删端口）时小图不跟着变。
 * 现在所有改动都会走 updateLegendPreview(i) 立即重绘。
 *
 * 用法: node scripts/e2e_legend_live_preview.js  (需先启动 python -m avcad ui --port 8900)
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8900";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");
const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };

(async () => {
  const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), {
    url: BASE + "/", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = () => {};
      w.performance = w.performance || { now: () => Date.now() };
      w.fetch = (u, o) => globalThis.fetch(BASE + u, o);
    },
  });
  const w = dom.window, d = w.document;
  await new Promise(r => w.addEventListener("load", r));
  await sleep(400);
  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const count = i => ({
    circles: d.querySelectorAll(`#lgpreview_${i} circle`).length,
    texts: d.querySelectorAll(`#lgpreview_${i} text`).length,
  });

  click($("btnParse")); await sleep(1400);
  click($("btnToStep3")); await sleep(1600);

  const i = 0;
  const before = count(i);
  console.log("  初始预览：", JSON.stringify(before));
  ok(before.circles > 0, "预览小图已渲染（circle " + before.circles + " 个）");

  // ① 点第一个「＋」增加端口数量
  const plusBtn = d.querySelectorAll(`#lgrows_${i} .prow`)[0].querySelectorAll("button.mini")[1];
  click(plusBtn); await sleep(250);
  const afterPlus = count(i);
  console.log("  点＋后：", JSON.stringify(afterPlus));
  ok(afterPlus.circles === before.circles + 1, `数量 +1 后圆点 ${before.circles} → ${afterPlus.circles}`);

  // ② 改标签文字
  const labelInput = d.querySelectorAll(`#lgrows_${i} .prow`)[0].querySelector("input[type=text]");
  labelInput.value = "TEST";
  labelInput.dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(250);
  const texts = [...d.querySelectorAll(`#lgpreview_${i} text`)].map(t => t.textContent);
  ok(texts.some(t => t.indexOf("TEST") === 0), "改标签后预览文字同步： " + JSON.stringify(texts.slice(0, 4)));

  // ③ 增加一类端口
  click($(`lgadd_${i}`)); await sleep(250);
  const afterAdd = count(i);
  ok(afterAdd.circles > afterPlus.circles, `新增一类端口后圆点 ${afterPlus.circles} → ${afterAdd.circles}`);

  // ④ 删除一类端口
  const delBtn = d.querySelectorAll(`#lgrows_${i} .prow`)[0].querySelector("button.mini.del");
  click(delBtn); await sleep(250);
  const afterDel = count(i);
  ok(afterDel.circles < afterAdd.circles, `删除一类后圆点 ${afterAdd.circles} → ${afterDel.circles}`);

  console.log("\nRESULT: " + (fails ? `FAIL ❌ (${fails})` : "PASS ✅"));
  dom.window.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
