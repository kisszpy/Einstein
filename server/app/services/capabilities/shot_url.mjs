// 用仓库 web/node_modules 里的 playwright-core 截一张整页图。
// 供后端 browser_shot 调用（Python 侧没装 playwright 时的兜底）。
// 用法：node shot_url.mjs <url> <out.png> [fullPage=1] [width=1280]
// 环境变量：EINSTEIN_NODE_MODULES（playwright-core 所在 node_modules）、EINSTEIN_CHROME_PATH（可选）
import { createRequire } from "node:module";
import process from "node:process";

const [url, out, fullPage = "1", width = "1280"] = process.argv.slice(2);
if (!url || !out) {
  console.error("usage: shot_url.mjs <url> <out.png> [fullPage] [width]");
  process.exit(2);
}

const nodeModules = process.env.EINSTEIN_NODE_MODULES;
if (!nodeModules) {
  console.error("EINSTEIN_NODE_MODULES is not set");
  process.exit(2);
}
const base = nodeModules.endsWith("/") || nodeModules.endsWith("\\") ? nodeModules : `${nodeModules}/`;
const require = createRequire(base);
const { chromium } = require("playwright-core");

const launch = {
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
};
if (process.env.EINSTEIN_CHROME_PATH) launch.executablePath = process.env.EINSTEIN_CHROME_PATH;

let browser;
try {
  browser = await chromium.launch(launch);
  const page = await browser.newPage({ viewport: { width: Number(width) || 1280, height: 900 } });
  await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
  const title = await page.title();
  const finalUrl = page.url();
  await page.screenshot({ path: out, fullPage: fullPage === "1" });
  console.log(JSON.stringify({ ok: true, title, final_url: finalUrl, path: out }));
} catch (err) {
  console.error(String((err && err.message) || err));
  process.exit(1);
} finally {
  if (browser) await browser.close();
}
