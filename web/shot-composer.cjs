const { chromium } = require("playwright-core");
const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C://Users//Admin//AppData//Local//ms-playwright//chromium-1208//chrome-win64//chrome.exe";
const BASE = "http://localhost:5173";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push("console: " + m.text()));

  await page.addInitScript(() => localStorage.setItem("theme", "light"));
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.click("button[type=submit]");
  await page.waitForURL("**/chat", { timeout: 15000 });
  await page.waitForTimeout(900);

  const chatBox = await page.locator("[data-composer-frame]").first().boundingBox();
  const chatCol = await page.locator(".conversation-column").last().boundingBox();
  await page.screenshot({ path: "shots-login/chat.png" });

  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const agentBox = await page.locator("[data-composer-frame]").first().boundingBox();
  const agentCol = await page.locator(".conversation-column").last().boundingBox();
  await page.screenshot({ path: "shots-login/agents.png" });

  const round = (b) => (b ? `x=${Math.round(b.x)} w=${Math.round(b.width)} h=${Math.round(b.height)}` : "NA");
  console.log("chat  composer:", round(chatBox), "| column:", round(chatCol));
  console.log("agent composer:", round(agentBox), "| column:", round(agentCol));
  console.log("宽度差:", chatBox && agentBox ? Math.abs(chatBox.width - agentBox.width) : "NA");
  console.log(errors.length ? "ERRORS:/n" + errors.join("\n") : "no page errors");
  await browser.close();
})();
