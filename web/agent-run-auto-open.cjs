// 真跑一次智能体：产物生成后右侧面板应自动打开。
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const PROJECT = "browser-demo";
const STAMP = Date.now();
const FILE = `auto-open-${STAMP}.txt`;
const SHOTS = "shots-agent-panel";

const ok = [];
const bad = [];
function check(name, pass, detail = "") {
  (pass ? ok : bad).push(name);
  console.log(`${pass ? "  PASS" : "  FAIL"}  ${name}${pass ? "" : "  <- " + detail}`);
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat|\/$/, { timeout: 20000 });

  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.locator("select").first().selectOption(PROJECT);
  await page.waitForTimeout(600);

  check("运行前面板是关着的", (await page.getByLabel("产物预览面板").count()) === 0);

  await page
    .locator("[data-agent-composer]")
    .fill(`在当前项目里写一个文件 ${FILE}，内容只有一行：auto open ${STAMP}。写完就结束，不要做别的。`);
  await page.getByLabel("发送").click();

  // 等运行结束：先确认进入「停止运行」，结束后变回「发送」
  await page.waitForSelector('button[aria-label="停止运行"]', { timeout: 20000 });
  await page.waitForSelector('button[aria-label="发送"]', { timeout: 180000 });
  await page.waitForTimeout(1200);

  const panel = page.getByLabel("产物预览面板");
  check("产物生成后面板自动打开", (await panel.count()) === 1, page.url());
  const text = await panel.innerText().catch(() => "");
  check("面板里正是刚生成的产物", text.includes(FILE), text.slice(0, 200));
  await page.screenshot({ path: `${SHOTS}/panel-auto-open.png` });

  await browser.close();
  console.log(`\n通过 ${ok.length} / ${ok.length + bad.length}`);
  if (bad.length) {
    console.log("失败：" + bad.join(" | "));
    process.exit(1);
  }
})();
