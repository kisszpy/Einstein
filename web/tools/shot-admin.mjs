/**
 * 系统管理页布局巡检：成员卡片头部按钮是否溢出。
 * 用法：node tools/shot-admin.mjs <token> [width]
 */
import { chromium } from "playwright-core";
import fs from "node:fs";

const CHROME =
  process.env.CHROME_PATH ||
  `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe`;

const token = process.argv[2];
const width = Number(process.argv[3] || 1440);
if (!token) throw new Error("need token");
const outDir = "c:/Users/Admin/WorkBuddy/2026-11-05-01-05-07/.workbuddy/shots";
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const ctx = await browser.newContext({ viewport: { width, height: 900 } });
const page = await ctx.newPage();

await page.goto("http://127.0.0.1:5173/login", { waitUntil: "domcontentloaded" });
await page.evaluate((t) => {
  localStorage.setItem("ai-platform:token", t);
  localStorage.setItem("ai-platform:workspace", "default");
}, token);
await page.goto("http://127.0.0.1:5173/admin", { waitUntil: "networkidle" });
await page.waitForTimeout(1800);

// 量一下按钮：是否被压成多行 / 超出卡片
const box = await page.locator("button", { hasText: "新增成员" }).first().boundingBox();
console.log("button box:", box);
await page.screenshot({ path: `${outDir}/admin-${width}.png` });
console.log("shot:", `${outDir}/admin-${width}.png`);

// 展开新增成员表单再看一眼
const btn = page.locator("button", { hasText: "新增成员" }).first();
if (await btn.count()) {
  await btn.click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${outDir}/admin-${width}-adding.png` });
  console.log("shot adding:", `${outDir}/admin-${width}-adding.png`);
}
await browser.close();
