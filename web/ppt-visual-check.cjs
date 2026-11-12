// AI PPT 设计台全链路视觉走查：确认卡预览 → 拧旋钮 → 灯箱 → 生成 → 缩略图 → 编辑器。
// 用法：node ppt-visual-check.cjs [sessionId]（需前端 5173 + 后端 8000 已启动）
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const SHOTS = "shots-ppt";
const SESSION_TITLE = process.argv[2] || "2026 智能工厂年度";
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

/** 懒加载图片要滚进视口才会解码，逐张 scrollIntoView 再等 naturalWidth。 */
async function decodeThumbs(page) {
  return page.evaluate(async () => {
    const imgs = [...document.querySelectorAll("img")].filter((i) => i.src.startsWith("data:image"));
    for (const img of imgs) img.scrollIntoView({ block: "center" });
    const t0 = Date.now();
    while (Date.now() - t0 < 20000) {
      if (imgs.length && imgs.every((i) => i.naturalWidth > 0)) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    return imgs.map((i) => ({ w: i.naturalWidth, h: i.naturalHeight, kb: Math.round(i.src.length / 1024) }));
  });
}

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

  console.log("\n== 登录并打开待确认会话");
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 20000 });
  await page.goto(`${BASE}/ppt-studio`, { waitUntil: "networkidle" });
  const pending = page.locator("aside[aria-label='演示会话'] [role='button']").filter({ hasText: SESSION_TITLE }).filter({ hasText: "待确认" }).first();
  if (await pending.count()) {
    await pending.click();
  } else {
    // 没有待确认会话就现开一个，保证走的是完整链路而不是历史结果
    await page.getByLabel("选择落盘项目").selectOption({ label: "ppt-e2e-demo" });
    await page.getByPlaceholder(/描述要做一份什么演示/).fill(PROMPT);
    await page.getByTitle(/开始生成大纲/).click();
  }
  await page.getByLabel("设计台").waitFor({ timeout: 240000 });
  check("确认卡打开设计台", true);

  const engineBadge = await page.getByLabel("设计台").innerText();
  check("实测排版引擎已启用", engineBadge.includes("实测排版"), engineBadge.slice(0, 120));
  const familyCount = await page.locator("[aria-label='设计台'] button[aria-pressed]").count();
  check("设计家族 ≥ 6", familyCount >= 6, `count=${familyCount}`);

  console.log("\n== 首屏实测预览");
  reset();
  await page.waitForFunction(
    () => [...document.querySelectorAll("img")].filter((i) => i.src.startsWith("data:image")).length >= 3,
    { timeout: 90000 },
  );
  let thumbs = await decodeThumbs(page);
  check("预览缩略图全部解码", thumbs.length >= 3 && thumbs.every((t) => t.w > 0), JSON.stringify(thumbs));
  check("缩略图为 16:9", thumbs.every((t) => Math.abs(t.w / t.h - 16 / 9) < 0.05), JSON.stringify(thumbs.map((t) => `${t.w}x${t.h}`)));
  await page.getByLabel("设计台").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/01-设计台-首屏.png` });
  await page.getByLabel("设计台").screenshot({ path: `${SHOTS}/02-设计台-局部.png` });
  check("首屏无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  check("首屏无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  check("首屏接口无 4xx/5xx", failedReqs.length === 0, failedReqs.slice(0, 3).join(" | "));

  console.log("\n== 旋钮实时重预览");
  reset();
  const before = await page.locator("[aria-label='设计台'] img").first().getAttribute("src");
  await page.locator("[aria-label='设计台'] button[aria-pressed]").filter({ hasText: "暮色" }).click();
  await page.waitForTimeout(400);
  await page.getByRole("slider", { name: "字号倍率" }).fill("1.1");
  await page.getByRole("button", { name: "强调色 #E8A03C" }).click();
  await page.getByRole("switch", { name: "母题装饰" }).click();
  await page.waitForFunction(
    (prev) => {
      const img = document.querySelector("[aria-label='设计台'] img");
      return img && img.getAttribute("src") !== prev;
    },
    before,
    { timeout: 90000 },
  );
  thumbs = await decodeThumbs(page);
  check("拧旋钮后预览重出图", thumbs.every((t) => t.w > 0), JSON.stringify(thumbs));
  const resetBtn = page.getByRole("button", { name: /^重置/ });
  check("重置按钮已激活", await resetBtn.isEnabled());
  await page.getByLabel("设计台").screenshot({ path: `${SHOTS}/03-设计台-暮色琥珀.png` });
  check("交互无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));

  console.log("\n== 灯箱");
  reset();
  await page.locator("[aria-label='设计台'] img").first().click();
  const lightbox = page.locator("[role='dialog'][aria-label^='第 ']");
  await lightbox.waitFor({ timeout: 8000 });
  const bigSize = await lightbox.locator("img").evaluate((i) => ({ w: i.naturalWidth, h: i.naturalHeight }));
  check("灯箱用的是全尺寸实测图", bigSize.w >= 1024, JSON.stringify(bigSize));
  await page.screenshot({ path: `${SHOTS}/04-灯箱.png` });
  const bigSrc = await lightbox.locator("img").getAttribute("src");
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(300);
  const nextSrc = await lightbox.locator("img").getAttribute("src");
  check("灯箱方向键翻页", bigSrc !== nextSrc);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check("Esc 关闭灯箱", (await lightbox.count()) === 0);
  check("灯箱无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));

  console.log("\n== 双主题");
  reset();
  const themeBtn = page.getByRole("button", { name: /切换到亮色|切换到暗色/ });
  const startLabel = await themeBtn.getAttribute("aria-label");
  const shotLight = startLabel === "切换到暗色";
  await page.getByLabel("设计台").scrollIntoViewIfNeeded();
  await page.getByLabel("设计台").screenshot({ path: `${SHOTS}/05-设计台-${shotLight ? "亮色" : "暗色"}.png` });
  await themeBtn.click();
  await page.waitForTimeout(700);
  await page.getByLabel("设计台").scrollIntoViewIfNeeded();
  await page.getByLabel("设计台").screenshot({ path: `${SHOTS}/05b-设计台-${shotLight ? "暗色" : "亮色"}.png` });
  const overflowOther = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("切换主题后无横向溢出", overflowOther <= 0, `overflow=${overflowOther}`);
  check("主题切换无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  await themeBtn.click();
  await page.waitForTimeout(400);

  console.log("\n== 窄屏 390px");
  reset();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/06-窄屏-整页.png`, fullPage: false });
  const overflowNarrow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("窄屏无横向溢出", overflowNarrow <= 1, `overflow=${overflowNarrow}`);
  // 只看「不溢出」会漏掉挤压：264px 会话栏留在流里时正文剩 110px，标题竖排也不溢出
  const consoleBox = await page.getByLabel("设计台").boundingBox();
  check("窄屏设计台占满正文宽度", consoleBox && consoleBox.width >= 340, JSON.stringify(consoleBox));
  const sidebarBox = await page.evaluate(() => {
    const aside = document.querySelector("aside[aria-label='演示会话']");
    if (!aside) return { present: false };
    const r = aside.getBoundingClientRect();
    return { present: true, inFlow: !!aside.offsetParent, w: Math.round(r.width) };
  });
  check("窄屏会话栏已收起（不占版面）", !sidebarBox.present || (!sidebarBox.inFlow && sidebarBox.w === 0), JSON.stringify(sidebarBox));
  const titleLines = await page.getByLabel("设计台").evaluate((sec) => {
    const span = [...sec.querySelectorAll("span")].find((s) => s.textContent.trim() === "设计台");
    return span ? Math.round(span.getBoundingClientRect().height) : -1;
  });
  check("窄屏设计台标题不竖排", titleLines > 0 && titleLines <= 24, `height=${titleLines}`);
  const composerBox = await page.getByPlaceholder(/描述要做一份什么演示/).boundingBox();
  check("窄屏输入框可用宽度", composerBox && composerBox.width >= 280, JSON.stringify(composerBox));
  const slidersReachable = await page.getByRole("slider", { name: "卡片圆角" }).isVisible();
  check("窄屏旋钮可达", slidersReachable);

  const drawerBtn = page.getByRole("button", { name: "会话", exact: true });
  check("窄屏有会话抽屉入口", await drawerBtn.isVisible());
  await drawerBtn.click();
  await page.waitForTimeout(350);
  const drawerBox = await page.evaluate(() => {
    const aside = document.querySelector("aside[aria-label='演示会话']");
    const r = aside?.getBoundingClientRect();
    return r ? { x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height) } : null;
  });
  check("抽屉展开后会话栏可见", drawerBox && drawerBox.x === 0 && drawerBox.w >= 240 && drawerBox.h > 400, JSON.stringify(drawerBox));
  await page.screenshot({ path: `${SHOTS}/06b-窄屏-会话抽屉.png` });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(350);
  const closedBox = await page.evaluate(() => {
    const aside = document.querySelector("aside[aria-label='演示会话']");
    return aside ? Math.round(aside.getBoundingClientRect().width) : 0;
  });
  check("Esc 关闭会话抽屉", closedBox === 0, `width=${closedBox}`);
  check("窄屏无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.waitForTimeout(500);

  console.log("\n== 确认并生成");
  reset();
  await page.getByRole("button", { name: "确认并生成" }).click();
  await page.getByRole("button", { name: "打开编辑器" }).waitFor({ timeout: 300000 });
  await page.waitForTimeout(1500);
  const resultText = await page.locator("main").innerText();
  check("结果卡标注实测排版", resultText.includes("实测排版"), resultText.slice(-400));
  const resultThumbs = await page.evaluate(async () => {
    const imgs = [...document.querySelectorAll("img")].filter((i) => i.src.includes("/api/ppt/thumbs/"));
    for (const img of imgs) img.scrollIntoView({ block: "center" });
    const t0 = Date.now();
    while (Date.now() - t0 < 20000) {
      if (imgs.length && imgs.every((i) => i.naturalWidth > 0)) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    return imgs.map((i) => ({ w: i.naturalWidth, h: i.naturalHeight, url: i.src.split("?")[0].slice(-24) }));
  });
  check("结果卡缩略图走票据取图且全部解码", resultThumbs.length >= 3 && resultThumbs.every((t) => t.w > 0), JSON.stringify(resultThumbs));
  await page.screenshot({ path: `${SHOTS}/07-结果卡.png` });
  check("生成链路无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  check("生成链路无 console 错误", realConsole().length === 0, realConsole().slice(0, 3).join(" | "));
  check("生成链路接口无 4xx/5xx", failedReqs.length === 0, failedReqs.slice(0, 4).join(" | "));

  const expand = page.getByRole("button", { name: /展开全部/ });
  if (await expand.count()) {
    const n0 = await page.locator("img[src*='/api/ppt/thumbs/']").count();
    await expand.click();
    await page.waitForTimeout(600);
    const n1 = await page.locator("img[src*='/api/ppt/thumbs/']").count();
    check("展开全部页生效", n1 > n0, `${n0} -> ${n1}`);
    await page.screenshot({ path: `${SHOTS}/08-结果卡-全部页.png` });
  }

  console.log("\n== 窄屏结果卡");
  reset();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(700);
  await page.getByRole("button", { name: "打开编辑器" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/08b-窄屏-结果卡.png` });
  const resultOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("窄屏结果卡无横向溢出", resultOverflow <= 1, `overflow=${resultOverflow}`);
  const dlBox = await page.getByRole("link", { name: "下载" }).first().boundingBox();
  const edBox = await page.getByRole("button", { name: "打开编辑器" }).boundingBox();
  check("窄屏下载/编辑器按钮可达", !!dlBox && !!edBox && dlBox.width >= 48 && edBox.width >= 80, JSON.stringify({ dlBox, edBox }));
  check("窄屏结果卡无 console 错误", realConsole().length === 0, realConsole().slice(0, 2).join(" | "));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.waitForTimeout(500);

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
  await page.waitForTimeout(9000);
  const frame = page.frames().find((f) => f.url().includes("/deckcraft/"));
  check("DeckCraft iframe 已挂载", !!frame, page.frames().map((f) => f.url()).join(" | "));
  if (frame) {
    const canvas = await frame.locator("canvas").count();
    check("DeckCraft 画布已渲染", canvas > 0, `canvas=${canvas}`);
  }
  await page.screenshot({ path: `${SHOTS}/09-编辑器.png` });
  check("编辑器无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n==== ${results.length - failed.length}/${results.length} PASS`);
  if (failed.length) {
    console.log("失败项：");
    for (const f of failed) console.log(`  - ${f.name}: ${f.detail}`);
    process.exit(1);
  }
})().catch((err) => {
  console.error("\n脚本异常：", err.message);
  process.exit(1);
});