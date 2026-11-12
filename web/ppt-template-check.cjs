// 上传模板 → 风格迁移全链路视觉走查：上传 → 抽取结论 → 设计台家族 → 4:3 实测预览
// → 拧旋钮 → 随机一版保持模板 → 灯箱 → 窄屏 → 生成 → 结果卡 → 下载 → 编辑器。
// 用法：node ppt-template-check.cjs（需前端 5173 + 后端 8000 已启动）
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const SHOTS = "shots-ppt-template";
const PROBE = path.resolve(__dirname, "../.workbuddy/ppt-template-probe");
const TEMPLATE_B = path.join(PROBE, "B-4x3-深色-青洋红.pptx");
const PACK_A_NAME = "A-16x9-浅内页-蓝橙";
const PACK_B_NAME = "B-4x3-深色-青洋红";
const PROMPT =
  "做一份 8 页的《2026 智能工厂年度复盘》，面向公司管理层，中文。关键数据直接用：" +
  "设备联网率 62%→91%，平均换线时间 48→19 分钟，一次良率 93.4%→97.8%，" +
  "单位制造成本下降 12%，交付准时率 88%→96%。不要再问我澄清问题，直接出大纲。";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${name}${ok ? "" : "  <- " + detail}`);
}

let pageErrors = [];
let consoleErrors = [];
let failedReqs = [];
const reset = () => {
  pageErrors = [];
  consoleErrors = [];
  failedReqs = [];
};
const realConsole = () =>
  consoleErrors.filter((e) => !/favicon|React DevTools|Allow attribute|integrity attribute/i.test(e));

/** 懒加载图片要滚进视口才会解码；同时量渲染框，验证 4:3 没被 16:9 的框裁掉。 */
async function decodeThumbs(page, scope = "[aria-label='设计台']") {
  return page.evaluate(async (sel) => {
    const root = document.querySelector(sel) || document;
    const imgs = [...root.querySelectorAll("img")].filter(
      (i) => i.src.startsWith("data:image") || i.src.includes("/api/ppt/thumbs/"),
    );
    for (const img of imgs) img.scrollIntoView({ block: "center" });
    const t0 = Date.now();
    while (Date.now() - t0 < 25000) {
      if (imgs.length && imgs.every((i) => i.naturalWidth > 0)) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    return imgs.map((i) => {
      const r = i.getBoundingClientRect();
      return {
        w: i.naturalWidth,
        h: i.naturalHeight,
        boxW: Math.round(r.width),
        boxH: Math.round(r.height),
        fit: getComputedStyle(i).objectFit,
      };
    });
  }, scope);
}

const ratioOf = (t) => t.w / t.h;
const isRatio = (t, want) => Math.abs(ratioOf(t) - want) < 0.05;
/** 框的比例必须跟图一致，否则 object-cover 会把页脚裁掉。 */
const boxMatches = (t) => Math.abs(t.boxW / t.boxH - ratioOf(t)) < 0.06;

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300));
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400)
      failedReqs.push(`${r.status()} ${r.url().replace(BASE, "")}`);
  });

  console.log("\n== 登录并打开 PPT 工作台");
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 20000 });
  await page.goto(`${BASE}/ppt-studio`, { waitUntil: "networkidle" });
  await page.getByLabel("选择落盘项目").selectOption({ label: "ppt-e2e-demo" });

  console.log("\n== 上传 4:3 深色模板");
  reset();
  // 空状态与输入条各有一个选择器（共用同一份 state），取输入条里那个：
  // 提交需求后空状态会卸载，后续步骤的定位才不会漂。
  const picker = page.getByTitle(/选择设计风格，或上传公司/).last();
  const fileInput = page.locator("input[type=file][accept='.pptx']").last();
  await picker.click();
  const popover = page.locator("div.absolute.bottom-full");
  await popover.waitFor({ timeout: 5000 });
  const listText = await popover.innerText();
  check("选择器列出已上传模板的抽取结论", /浅色 · 16:9 · 主色 #1E6FD9/.test(listText), listText.slice(0, 300));
  check("模板行不再显示 Office 出厂主题色", !/4F81BD/i.test(listText), listText.slice(0, 300));
  await fileInput.setInputFiles(TEMPLATE_B);
  await page.getByText(/已就绪/).waitFor({ timeout: 120000 });
  const readyText = await popover.innerText();
  check("上传回执带抽取结论（深色 · 4:3 · 主色）", /已就绪 · 深色 · 4:3 · 主色 #00C2A8/.test(readyText), readyText.slice(-260));
  await page.screenshot({ path: `${SHOTS}/01-上传回执.png` });
  check("上传无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  check("上传接口无 4xx/5xx", failedReqs.length === 0, failedReqs.slice(0, 3).join(" | "));
  await page.mouse.click(700, 120);
  await page.waitForTimeout(300);
  const trigger = await picker.innerText();
  check("触发按钮显示当前模板与比例", trigger.includes("4:3"), trigger);

  console.log("\n== 提交需求 → 确认卡");
  reset();
  await page.getByPlaceholder(/描述要做一份什么演示/).fill(PROMPT);
  await page.getByTitle(/开始生成大纲/).click();
  await page.getByLabel("设计台").waitFor({ timeout: 300000 });
  check("确认卡打开设计台", true);
  const console_ = page.getByLabel("设计台");
  const head = await console_.innerText();
  check("设计台标注按上传模板重绘", head.includes(PACK_B_NAME), head.slice(0, 200));
  check("实测排版引擎已启用", head.includes("实测排版"), head.slice(0, 160));

  console.log("\n== 模板家族与适配说明");
  const chipB = console_.locator("button[aria-pressed]").filter({ hasText: PACK_B_NAME }).first();
  check("公司模板排在家族列表里", (await chipB.count()) > 0);
  check("模板家族默认已选中", (await chipB.getAttribute("aria-pressed")) === "true");
  const brief = await console_.innerText();
  check("适配说明列出正文字体", brief.includes("Noto Serif SC"), brief.slice(0, 400));
  check("适配说明列出画布比例", brief.includes("画布 4:3"), brief.slice(0, 400));
  check("适配说明列出版心边距", /版心边距 \d+px/.test(brief), brief.slice(0, 400));
  check("字体替换写在明面上", brief.includes("处适配") && brief.includes("阿里巴巴普惠体"), brief.slice(0, 600));
  await console_.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/02-设计台-模板家族.png` });
  await console_.screenshot({ path: `${SHOTS}/03-设计台-局部.png` });

  console.log("\n== 4:3 实测预览");
  reset();
  await page.waitForFunction(
    () => [...document.querySelectorAll("[aria-label='设计台'] img")].filter((i) => i.src.startsWith("data:image")).length >= 3,
    { timeout: 120000 },
  );
  let thumbs = await decodeThumbs(page);
  check("预览缩略图全部解码", thumbs.length >= 3 && thumbs.every((t) => t.w > 0), JSON.stringify(thumbs));
  check("预览缩略图是模板的 4:3", thumbs.every((t) => isRatio(t, 4 / 3)), JSON.stringify(thumbs.map((t) => `${t.w}x${t.h}`)));
  check("预览框比例跟图一致（页脚没被裁）", thumbs.every(boxMatches), JSON.stringify(thumbs.map((t) => `${t.boxW}x${t.boxH}/${t.w}x${t.h} ${t.fit}`)));
  check("预览无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  check("预览接口无 4xx/5xx", failedReqs.length === 0, failedReqs.slice(0, 3).join(" | "));

  console.log("\n== 模板上照样拧旋钮");
  reset();
  const before = await console_.locator("img").first().getAttribute("src");
  await page.getByRole("slider", { name: "卡片圆角" }).fill("26");
  await page.getByRole("slider", { name: "字号倍率" }).fill("1.08");
  await page.waitForFunction(
    (prev) => {
      const img = document.querySelector("[aria-label='设计台'] img");
      return img && img.getAttribute("src") !== prev;
    },
    before,
    { timeout: 120000 },
  );
  thumbs = await decodeThumbs(page);
  check("拧旋钮后模板预览重出图", thumbs.every((t) => t.w > 0), JSON.stringify(thumbs));
  check("拧旋钮后仍是 4:3", thumbs.every((t) => isRatio(t, 4 / 3)), JSON.stringify(thumbs.map((t) => `${t.w}x${t.h}`)));
  check("重置按钮已激活", await page.getByRole("button", { name: /^重置/ }).isEnabled());
  await console_.screenshot({ path: `${SHOTS}/04-设计台-拧过旋钮.png` });
  check("旋钮交互无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));

  console.log("\n== 随机一版不能摇走用户的模板");
  await page.getByRole("button", { name: /随机一版/ }).click();
  await page.waitForTimeout(600);
  check("随机一版后仍选中上传模板", (await chipB.getAttribute("aria-pressed")) === "true");

  console.log("\n== 切到 16:9 模板：画布跟着换");
  reset();
  const chipA = console_.locator("button[aria-pressed]").filter({ hasText: PACK_A_NAME }).first();
  await chipA.click();
  await page.waitForFunction(
    () => {
      const imgs = [...document.querySelectorAll("[aria-label='设计台'] img")].filter((i) => i.naturalWidth > 0);
      return imgs.length >= 3 && imgs.every((i) => Math.abs(i.naturalWidth / i.naturalHeight - 16 / 9) < 0.05);
    },
    { timeout: 120000 },
  );
  thumbs = await decodeThumbs(page);
  check("换成 16:9 模板后预览转 16:9", thumbs.every((t) => isRatio(t, 16 / 9)), JSON.stringify(thumbs.map((t) => `${t.w}x${t.h}`)));
  check("16:9 预览框也没裁图", thumbs.every(boxMatches), JSON.stringify(thumbs.map((t) => `${t.boxW}x${t.boxH} ${t.fit}`)));
  const briefA = await console_.innerText();
  check("浅内页模板说明标出深封面已统一", briefA.includes("封面为深底"), briefA.slice(0, 600));
  await console_.screenshot({ path: `${SHOTS}/05-设计台-16x9模板.png` });
  check("换家族无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  // 回到 4:3 模板，生成走的是「保留原比例」这条路
  await chipB.click();
  await page.waitForFunction(
    () => {
      const imgs = [...document.querySelectorAll("[aria-label='设计台'] img")].filter((i) => i.naturalWidth > 0);
      return imgs.length >= 3 && imgs.every((i) => Math.abs(i.naturalWidth / i.naturalHeight - 4 / 3) < 0.05);
    },
    { timeout: 120000 },
  );

  console.log("\n== 灯箱");
  reset();
  await console_.locator("img").first().click();
  const lightbox = page.locator("[role='dialog'][aria-label^='第 ']");
  await lightbox.waitFor({ timeout: 8000 });
  const big = await lightbox.locator("img").evaluate((i) => ({ w: i.naturalWidth, h: i.naturalHeight }));
  check("灯箱用的是全尺寸实测图", big.w >= 900, JSON.stringify(big));
  check("灯箱保持 4:3", isRatio(big, 4 / 3), JSON.stringify(big));
  await page.screenshot({ path: `${SHOTS}/06-灯箱-4x3.png` });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check("Esc 关闭灯箱", (await lightbox.count()) === 0);

  console.log("\n== 窄屏 390px");
  reset();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(900);
  await console_.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/07-窄屏-设计台.png` });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("窄屏无横向溢出", overflow <= 1, `overflow=${overflow}`);
  const box = await console_.boundingBox();
  check("窄屏设计台占满正文宽度", box && box.width >= 340, JSON.stringify(box));
  check("窄屏模板适配说明可见", await page.getByText(/画布 4:3/).first().isVisible());
  check("窄屏旋钮可达", await page.getByRole("slider", { name: "卡片圆角" }).isVisible());
  const narrowThumbs = await decodeThumbs(page);
  check("窄屏 4:3 预览没被裁", narrowThumbs.every(boxMatches), JSON.stringify(narrowThumbs.map((t) => `${t.boxW}x${t.boxH} ${t.fit}`)));
  check("窄屏无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.waitForTimeout(500);

  console.log("\n== 确认并生成");
  reset();
  await page.getByRole("button", { name: "确认并生成" }).click();
  await page.getByRole("button", { name: "打开编辑器" }).waitFor({ timeout: 420000 });
  await page.waitForTimeout(1500);
  const resultText = await page.locator("main").innerText();
  check("结果卡标注模板重绘", /模板/.test(resultText), resultText.slice(-500));
  check("结果卡带上字体替换说明", resultText.includes("阿里巴巴普惠体"), resultText.slice(-700));
  const resultThumbs = await decodeThumbs(page, "main");
  check("结果卡缩略图全部解码", resultThumbs.length >= 3 && resultThumbs.every((t) => t.w > 0), JSON.stringify(resultThumbs.slice(0, 4)));
  check("成稿缩略图是 4:3", resultThumbs.every((t) => isRatio(t, 4 / 3)), JSON.stringify(resultThumbs.map((t) => `${t.w}x${t.h}`)));
  check("结果卡缩略图没被裁", resultThumbs.every(boxMatches), JSON.stringify(resultThumbs.map((t) => `${t.boxW}x${t.boxH} ${t.fit}`)));
  await page.screenshot({ path: `${SHOTS}/08-结果卡.png` });
  check("生成链路无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  check("生成链路无 console 错误", realConsole().length === 0, realConsole().slice(0, 3).join(" | "));
  check("生成链路接口无 4xx/5xx", failedReqs.length === 0, failedReqs.slice(0, 4).join(" | "));

  const expand = page.getByRole("button", { name: /展开全部/ });
  if (await expand.count()) {
    const n0 = await page.locator("img[src*='/api/ppt/thumbs/']").count();
    await expand.click();
    await page.waitForTimeout(800);
    const n1 = await page.locator("img[src*='/api/ppt/thumbs/']").count();
    check("展开全部页生效", n1 > n0, `${n0} -> ${n1}`);
    const all = await decodeThumbs(page, "main");
    check("全部页都是 4:3 且没被裁", all.every((t) => isRatio(t, 4 / 3) && boxMatches(t)), JSON.stringify(all.map((t) => `${t.w}x${t.h}/${t.boxW}x${t.boxH}`)));
    await page.screenshot({ path: `${SHOTS}/09-结果卡-全部页.png` });
  }

  console.log("\n== 下载 pptx");
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 60000 }),
    page.getByRole("link", { name: "下载" }).first().click(),
  ]);
  const file = `${SHOTS}/${download.suggestedFilename()}`;
  await download.saveAs(file);
  check("pptx 下载成功", fs.statSync(file).size > 20000, `${fs.statSync(file).size} bytes`);
  console.log(`  saved ${file}`);

  console.log("\n== 打开 DeckCraft 编辑器");
  reset();
  await page.getByRole("button", { name: "打开编辑器" }).click();
  await page.waitForTimeout(11000);
  const frame = page.frames().find((f) => f.url().includes("/deckcraft/"));
  check("DeckCraft iframe 已挂载", !!frame, page.frames().map((f) => f.url()).join(" | "));
  if (frame) {
    const canvas = await frame.locator("canvas").count();
    check("DeckCraft 画布已渲染", canvas > 0, `canvas=${canvas}`);
  }
  await page.screenshot({ path: `${SHOTS}/10-编辑器-4x3.png` });
  check("编辑器无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n==== ${results.length - failed.length}/${results.length} PASS`);
  fs.writeFileSync(`${SHOTS}/report.json`, JSON.stringify(results, null, 2));
  if (failed.length) {
    console.log("失败项：");
    for (const f of failed) console.log(`  - ${f.name}: ${f.detail}`);
    process.exit(1);
  }
})().catch((err) => {
  console.error("\n脚本异常：", err.message);
  process.exit(1);
});