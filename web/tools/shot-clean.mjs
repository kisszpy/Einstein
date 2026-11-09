/**
 * 清理后巡检截图：首页、专家库、技能库。
 * 用法：node tools/shot-clean.mjs <token>
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
  if (m.type() === "error") console.log("[browser-error]", m.text().slice(0, 160));
});

await page.goto("http://127.0.0.1:5173/login", { waitUntil: "domcontentloaded" });
await page.evaluate((t) => {
  localStorage.setItem("ai-platform:token", t);
  localStorage.setItem("ai-platform:workspace", "default");
}, token);

for (const [name, path] of [["home", "/"], ["experts", "/experts"], ["skills", "/skills"], ["kb", "/knowledge"]]) {
  await page.goto(`http://127.0.0.1:5173${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1600);
  await page.screenshot({ path: `${outDir}/clean-${name}.png` });
  console.log("shot", name);
}

await browser.close();
