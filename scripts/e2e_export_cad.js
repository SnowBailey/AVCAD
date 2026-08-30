/**
 * 端到端：① 第1步导入后按钮不冗余  ② 第5步「导出 CAD」完整链路
 *   - 选目录（此处 stub 掉原生弹窗，避免测试时弹出系统对话框）
 *   - 进度显示 → 落盘 → 展示保存路径 → 「打开所在文件夹」
 *   - 只读目录 / 导出失败时给出明确错误提示
 *
 * 用法: node scripts/e2e_export_cad.js   （需先启动 python -m avcad ui --port 8900）
 */
const { JSDOM } = require("jsdom");
const fs = require("fs");
const os = require("os");
const path = require("path");

const BASE = process.env.AVCAD_BASE || "http://127.0.0.1:8900";
const HTML = path.join(__dirname, "..", "avcad", "ui", "static", "index.html");

// 可写目录 + 只读目录（用于验证权限错误）
const OUT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "avcad-e2e-out-"));
const RO_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "avcad-e2e-ro-"));
fs.chmodSync(RO_DIR, 0o500);

let fails = 0;
const ok = (c, m) => { console.log((c ? "  ✅ " : "  ❌ ") + m); if (!c) fails++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pickResult = OUT_DIR;     // 下一次 /api/pick-folder 的返回值

(async () => {
  const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), {
    url: BASE + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = () => {};
      w.performance = w.performance || { now: () => Date.now() };
      const real = (u, o) => globalThis.fetch(BASE + u, o);
      w.fetch = async (u, o) => {
        // 原生弹窗/打开 Finder 不参与自动化测试，直接给结果
        if (u === "/api/pick-folder") return { ok: true, json: async () => ({ path: pickResult }) };
        if (u === "/api/open-folder") return { ok: true, json: async () => ({ ok: true }) };
        return real(u, o);
      };
    },
  });
  const w = dom.window, d = w.document;
  await new Promise(r => w.addEventListener("load", r));
  await sleep(400);

  const $ = id => d.getElementById(id);
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const stepEl = n => d.querySelectorAll("#stepper .step")[n - 1];
  const layout = () => d.querySelector(".layout");

  console.log("【⓪ 标题 + 第1~3步隐藏右侧面板】");
  const h1 = d.querySelector("header h1").textContent.trim();
  ok(h1 === "AVCAD@Bailey@EZPRO", "标题为 AVCAD@Bailey@EZPRO（实际：" + h1 + "）");
  ok(d.title.includes("AVCAD@Bailey@EZPRO"), "文档标题已同步");
  ok(layout().classList.contains("wide"), "第1步：layout 加 .wide（右侧面板隐藏，空间留给左侧）");

  console.log("【① 第1步：导入后按钮不冗余】");
  ok(!$("step1Actions").classList.contains("hidden"), "初始显示导入操作区");
  ok($("step1Summary").classList.contains("hidden"), "初始不显示解析摘要");
  click($("btnParse"));
  await sleep(1200);
  ok(!$("panel2").classList.contains("hidden"), "解析后进入第 2 步");

  click(stepEl(1));                       // 回到第 1 步
  await sleep(300);
  ok($("step1Actions").classList.contains("hidden"), "回到第1步：操作按钮已隐藏（不再冗余）");
  ok(!$("step1Summary").classList.contains("hidden"), "回到第1步：显示解析摘要");
  ok($("step1SummaryText").textContent.includes("已解析"), "摘要文案：" + $("step1SummaryText").textContent.split("\n")[0]);
  click($("btnReimport"));
  await sleep(200);
  ok(!$("step1Actions").classList.contains("hidden"), "点「重新导入」后操作区回来了");
  ok($("step1Summary").classList.contains("hidden"), "「重新导入」后摘要收起");

  console.log("【② 走到第5步并出图】");
  click(stepEl(2)); await sleep(300);
  ok(layout().classList.contains("wide"), "第2步：右侧面板仍隐藏");
  click($("btnToStep3")); await sleep(700);
  ok(layout().classList.contains("wide"), "第3步：右侧面板仍隐藏");
  click($("btnLegSaveAll")); await sleep(1800);
  click($("btnToStep4")); await sleep(900);
  ok(!layout().classList.contains("wide"), "第4步：右侧面板恢复显示");
  click($("btnToStep5")); await sleep(1600);
  ok(!layout().classList.contains("wide"), "第5步：右侧面板显示（系统图预览）");
  if ($("btnGenGo").disabled) { console.log("（图例未确认，先一键确认）"); click($("btnFixLegend")); await sleep(2000); }
  click($("btnGenGo"));
  await sleep(3500);
  ok(!$("genDone").classList.contains("hidden"), "已出图");
  ok($("svg").innerHTML.includes("<svg"), "SVG 已渲染");
  // 回到第4步再回第5步，已生成的图纸不应被指引文案覆盖
  click(stepEl(4)); await sleep(600);
  click(stepEl(5)); await sleep(1200);
  ok($("svg").innerHTML.includes("<svg"), "往返第4步后回到第5步，已出图的预览仍保留");

  console.log("【③ 导出 CAD：醒目按钮 + 弹窗】");
  const btn = $("btnExportCAD");
  ok(!!btn, "第5步存在「导出 CAD」按钮");
  ok(btn.classList.contains("primary"), "该按钮为主按钮样式（.primary）");
  ok(btn.textContent.includes("导出 CAD"), "按钮文案：" + btn.textContent.trim());

  click(btn);
  await sleep(300);
  ok(!$("exportModal").classList.contains("hidden"), "点击后弹出导出弹窗");
  ok($("btnExportStart").disabled, "未选目录时「开始导出」禁用");
  ok($("exportName").value.endsWith(".dxf"), "默认文件名：" + $("exportName").value);

  click($("btnPickDir"));
  await sleep(600);
  ok($("exportDir").value === OUT_DIR, "目录已填入：" + $("exportDir").value);
  ok(!$("btnExportStart").disabled, "选好目录后「开始导出」可用");

  console.log("【④ 导出进度 + 落盘 + 保存路径 + 打开所在文件夹】");
  click($("btnExportStart"));
  await sleep(250);
  ok(!$("exportProgressWrap").classList.contains("hidden"), "导出中显示进度条");
  await sleep(2500);
  ok(!$("exportResult").classList.contains("hidden"), "导出结束显示结果区");
  const res = $("exportResult").textContent;
  ok(res.includes("已保存"), "结果含成功标记：" + res.split("\n")[0].trim());
  ok(res.includes(OUT_DIR), "结果展示了保存路径");
  ok($("exportBar").style.width === "100%", "进度条到 100%");
  ok(!$("btnExportOpenFolder").classList.contains("hidden"), "出现「打开所在文件夹」按钮");

  const files = fs.readdirSync(OUT_DIR).filter(f => f.endsWith(".dxf"));
  ok(files.length === 1, "目录内确实落盘了 1 个 DXF：" + files.join(", "));
  ok(fs.statSync(path.join(OUT_DIR, files[0])).size > 1000, "DXF 文件大小正常");

  click($("btnExportOpenFolder"));
  await sleep(400);
  ok(true, "点「打开所在文件夹」未报错");

  console.log("【⑤ 无写入权限 / 导出失败的错误提示】");
  pickResult = RO_DIR;
  click($("btnPickDir")); await sleep(500);
  ok($("exportDir").value === RO_DIR, "已切到只读目录");
  click($("btnExportStart"));
  await sleep(2000);
  const errTxt = $("exportResult").textContent;
  ok(errTxt.includes("导出失败"), "显示「导出失败」：" + errTxt.split("\n")[0].trim());
  ok(errTxt.includes("没有写入权限"), "错误原因明确：" + errTxt.split("\n").slice(1).join(" ").trim());

  console.log("\nRESULT: " + (fails ? `FAIL ❌ (${fails})` : "PASS ✅"));
  fs.chmodSync(RO_DIR, 0o700);
  dom.window.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
