const { chromium } = require("playwright-core");
const path = require("path");
const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C://Users//Admin//AppData//Local//ms-playwright//chromium-1208//chrome-win64//chrome.exe";
const BASE = "http://localhost:5173";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const errors = [];
  for (const theme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => errors.push(`[${theme}] ${e.message}`));
    page.on("console", (m) => m.type() === "error" && errors.push(`[${theme}] console: ${m.text()}`));
    await page.addInitScript((t) => {
      localStorage.setItem("theme", t);
      localStorage.removeItem("ai-platform:token");
    }, theme);
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `shots-login/${theme}.png`, fullPage: false });
    // 聚焦密码框并展开提示，抓一张交互态
    if (theme === "light") {
      await page.fill("#username", "admin");
      await page.fill("#password", "wrongpass");
      await page.click("button[type=submit]");
      await page.waitForTimeout(900);
      await page.screenshot({ path: `shots-login/light-error.png` });
    }
    await ctx.close();
  }
  // 移动端
  const m = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mp = await m.newPage();
  await mp.addInitScript(() => localStorage.setItem("theme", "light"));
  await mp.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await mp.waitForTimeout(600);
  await mp.screenshot({ path: "shots-login/mobile.png" });
  await m.close();

  await browser.close();
  console.log(errors.length ? "ERRORS:/n" + errors.join("\n") : "no page errors");
})();
