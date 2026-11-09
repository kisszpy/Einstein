/**
 * 截图验证 RBAC 界面：空间切换器（个人空间/角色/成员数）+ 空间成员弹层。
 * 用法：node tools/shot-rbac.mjs <token>
 */
import { chromium } from "playwright-core";
import fs from "node:fs";

const CHROME =
  process.env.CHROME_PATH ||
  `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe`;

const token = process.argv[2];
if (!token) throw new Error("need token");
const outDir = "c:/Users/Admin/WorkBuddy/2026-11-05-01-05-07/.workbuddy/shots";
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.log("[browser-error]", m.text().slice(0, 200));
});

// 先落到同源页面，再写 localStorage（about:blank 上下文没有 localStorage），然后刷新
await page.goto("http://127.0.0.1:5173/login", { waitUntil: "domcontentloaded" });
await page.evaluate((t) => {
  localStorage.setItem("ai-platform:token", t);
  localStorage.setItem("ai-platform:workspace", "default");
}, token);
await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1800);
await page.screenshot({ path: `${outDir}/rbac-1-home.png` });
console.log("shot 1: home");

// 打开工作区切换器
const trigger = page.locator("button", { hasText: /工作区|空间|Peter|平台管理员/ }).first();
await trigger.click({ timeout: 5000 }).catch(() => page.mouse.click(240, 40));
await page.waitForTimeout(800);
await page.screenshot({ path: `${outDir}/rbac-2-switcher.png` });
console.log("shot 2: switcher");

// 点「成员管理」
const manage = page.locator("button", { hasText: "成员管理" }).first();
if (await manage.count()) {
  await manage.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${outDir}/rbac-3-members.png` });
  console.log("shot 3: members dialog");
} else {
  console.log("no 成员管理 entry found");
}

await browser.close();
