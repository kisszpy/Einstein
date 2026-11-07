// 智能体页验收：deepagents 引擎 → 计划块 + 工具步骤 + 产物卡片
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const SHOT = "C:\\Users\\Admin\\WorkBuddy\\2026-11-05-01-05-07\\web\\shot-agent-plan.png";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await page.fill("#username", "admin");
    await page.fill("#password", "admin123");
    await page.press("#password", "Enter");
    await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 });

    await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
    const box = page.locator("textarea").first();
    await box.click();
    await box.fill(
      "这是多步任务，先用 write_todos 列一份 4 步计划（盘点现状→设计→生成 PLAN-DEMO.md→复核），" +
        "再逐步执行。项目用 deep-probe，最终产出 deep-probe/PLAN-DEMO.md。",
    );
    await page.keyboard.press("Enter");

    // 计划块：标题「计划」+ n/m 计数
    const plan = page.getByText("计划", { exact: true }).first();
    await plan.waitFor({ timeout: 180000 });
    const planText = await page.locator("li", { hasText: "复核" }).first().innerText().catch(() => "");
    console.log("PLAN_OK", planText.trim().slice(0, 40));

    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOT, fullPage: false });
    console.log("SHOT", fs.existsSync(SHOT));
    console.log("ERRORS", errors.length ? errors.slice(0, 3) : "none");
  } catch (err) {
    console.log("FAIL", String(err).slice(0, 300));
    await page.screenshot({ path: SHOT.replace(".png", "-fail.png") }).catch(() => {});
    console.log("ERRORS", errors.slice(0, 3));
  } finally {
    await browser.close();
  }
})();
