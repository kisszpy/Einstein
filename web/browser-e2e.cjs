// 浏览器端到端：登录 → 智能体页 → 跑任务 → 等完成 → 截图。
// 用真实 Chromium（不走 bash 沙箱的网络层），验证 SSE 在浏览器路径下是否完整。
const { chromium } = require("playwright-core");

const CHROMIUM = "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROMIUM,
    headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));
  page.on("response", (r) => { if (r.url().includes("/api/agent/run")) console.log("[run resp]", r.status()); });
  page.on("console", (m) => { if (m.type() === "error") console.log("[console]", m.text().slice(0, 200)); });

  // 登录
  await page.goto(BASE + "/login", { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.click('button[type="submit"]');
  await page.waitForURL("**/chat**", { timeout: 15000 }).catch(() => {});
  console.log("after login url:", page.url());

  // 进智能体页
  await page.goto(BASE + "/agents", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "shots/agents-page.png", fullPage: false });

  // 填任务并运行
  const taskBox = page.locator("textarea").first();
  await taskBox.fill("创建一个名为 browser-e2e 的项目，写 hello.py 打印 1 到 5 的平方，运行验证后汇报。");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  console.log("run clicked");

  // 轮询等待完成：运行中… -> 运行
  const t0 = Date.now();
  let finished = false;
  while (Date.now() - t0 < 240000) {
    await page.waitForTimeout(3000);
    const btn = await page.getByRole("button", { name: /运行$|运行中/ }).first().innerText();
    if (btn.trim() === "运行") { finished = true; break; }
  }
  await page.screenshot({ path: "shots/agents-run-done.png", fullPage: true });
  const text = await page.locator("body").innerText();
  const hasDone = finished && /browser-e2e/.test(text);
  console.log("elapsed:", Math.round((Date.now() - t0) / 1000) + "s", "finished:", finished, "project-in-history:", /browser-e2e/.test(text));
  console.log(text.slice(-1200));
  await browser.close();
})();
