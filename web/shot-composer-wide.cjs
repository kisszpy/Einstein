const { chromium } = require("playwright-core");
const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C://Users//Admin//AppData//Local//ms-playwright//chromium-1208//chrome-win64//chrome.exe";
const BASE = "http://localhost:5173";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  for (const vp of [{ width: 1920, height: 1080 }, { width: 1600, height: 900 }]) {
    const ctx = await browser.newContext({ viewport: vp });
    const page = await ctx.newPage();
    await page.addInitScript(() => localStorage.setItem("theme", "light"));
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await page.fill("#username", "admin");
    await page.fill("#password", "admin123");
    await page.click("button[type=submit]");
    await page.waitForURL("**/chat", { timeout: 15000 });
    await page.waitForTimeout(800);
    const a = await page.locator("[data-composer-frame]").first().boundingBox();
    if (vp.width === 1920) await page.screenshot({ path: "shots-login/chat-wide.png" });
    await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    const b = await page.locator("[data-composer-frame]").first().boundingBox();
    if (vp.width === 1920) await page.screenshot({ path: "shots-login/agents-wide.png" });
    const r = (x) => (x ? `w=${Math.round(x.width)} x=${Math.round(x.x)}` : "NA");
    console.log(`视口 ${vp.width}: chat ${r(a)} | agent ${r(b)} | 差 ${Math.abs(a.width - b.width)}`);
    await ctx.close();
  }
  await browser.close();
})();
