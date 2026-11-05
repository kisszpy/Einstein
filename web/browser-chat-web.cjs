// 浏览器端到端：验证对话页的按需联网（开关 + 状态条 + 来源面板）
const { chromium } = require("playwright-core");

const CHROMIUM = process.env.CHROMIUM_PATH || "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));

  // 登录
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat|\/$/, { timeout: 20000 });
  await page.goto(`${BASE}/chat`, { waitUntil: "networkidle" });
  console.log("已进入对话页");

  const toggle = page.locator('button[title="联网模式：自动 → 始终 → 关闭"]');
  const mode0 = (await toggle.innerText()).trim();
  console.log("初始联网模式:", mode0);

  // 循环一圈确认三态
  const modes = [mode0];
  for (let i = 0; i < 3; i++) {
    await toggle.click();
    await page.waitForTimeout(120);
    modes.push((await toggle.innerText()).trim());
  }
  console.log("三态循环:", modes.join(" -> "));

  // 回到「自动」
  while (!(await toggle.innerText()).includes("自动")) {
    await toggle.click();
    await page.waitForTimeout(100);
  }

  // 发一个时效问题
  const box = page.locator("textarea");
  await box.fill("今天杭州天气怎么样");
  await page.getByRole("button", { name: "发送" }).click();
  console.log("已发送时效问题");

  // 等待联网状态出现
  await page.waitForTimeout(1500);
  const sawSearching = await page.getByText(/联网检索|判断是否需要联网|已联网/).count();
  console.log("联网状态条出现:", sawSearching > 0);
  await page.screenshot({ path: "shots/chat-web-running.png" });

  // 等回答结束（发送按钮恢复）
  const t0 = Date.now();
  let finished = false;
  while (Date.now() - t0 < 180000) {
    await page.waitForTimeout(1500);
    if ((await page.getByRole("button", { name: "停止" }).count()) === 0) {
      finished = true;
      break;
    }
  }
  console.log("回答完成:", finished, "耗时:", Math.round((Date.now() - t0) / 1000) + "s");

  // 展开来源
  const webBtn = page.getByRole("button", { name: /已联网 ·/ }).first();
  const hasSources = (await webBtn.count()) > 0;
  console.log("来源面板存在:", hasSources);
  if (hasSources) {
    console.log("来源摘要:", (await webBtn.innerText()).trim());
    await webBtn.click();
    await page.waitForTimeout(400);
    const links = await page.locator("ol li a").allInnerTexts();
    console.log("来源条目:", links.slice(0, 4));
  }
  await page.screenshot({ path: "shots/chat-web-done.png" });

  // 关闭模式：问同样的问题，确认不再联网
  while (!(await toggle.innerText()).includes("关闭")) {
    await toggle.click();
    await page.waitForTimeout(100);
  }
  await box.fill("今天杭州天气怎么样");
  await page.getByRole("button", { name: "发送" }).click();
  console.log("已用关闭模式重发");
  const t1 = Date.now();
  while (Date.now() - t1 < 120000) {
    await page.waitForTimeout(1500);
    if ((await page.getByRole("button", { name: "停止" }).count()) === 0) break;
  }
  const bodyText = await page.locator("body").innerText();
  const lastBubble = bodyText.split("今天杭州天气怎么样").pop() || "";
  console.log("关闭模式下出现联网来源:", /已联网 ·/.test(lastBubble.slice(0, 800)));
  await page.screenshot({ path: "shots/chat-web-off.png" });

  await browser.close();
})();
