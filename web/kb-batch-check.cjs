// 批量导入 UI 验收：选目录 → 预览清单 → 导入 → 进度 → 文档列表
// 用法：node kb-batch-check.cjs   （需 5173 前端 + 8000 后端 + worker 已启动）
const { chromium } = require("playwright-core");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const API = "http://127.0.0.1:8000";

/** 现场生成一个带子目录的语料目录，避免把测试数据提交进仓库 */
function makeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kb-batch-"));
  const write = (rel, text) => {
    const target = path.join(root, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, text);
  };
  write("产品/需求.md", "# 批量需求\n目录导入要能一次吃下一个文件夹。\n");
  write("产品/接口/规范.md", "# 接口规范\nGET /api/kb 返回列表。\n");
  write("制度/报销.txt", "差旅报销 10 个工作日内提交。\n");
  write("安装包.exe", "x");
  return root;
}

const DIR = makeFixture();
process.on("exit", () => fs.rmSync(DIR, { recursive: true, force: true }));

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${name}${ok ? "" : "  <- " + detail}`);
}

async function api(path, opts = {}, token) {
  const res = await fetch(API + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  });
  return { status: res.status, body: await res.json().catch(() => null) };
}

(async () => {
  const login = await api("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username: "admin", password: "admin123" }),
  });
  const token = login.body.token;
  const kbName = `批量UI-${Math.random().toString(16).slice(2, 7)}`;
  const kb = (await api("/api/kb", { method: "POST", body: JSON.stringify({ name: kbName }) }, token)).body;

  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
    await page.fill("#username", "admin");
    await page.fill("#password", "admin123");
    await page.press("#password", "Enter");
    await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 15000 });

    await page.goto(`${BASE}/kb`, { waitUntil: "networkidle" });
    await page.getByText(kbName, { exact: false }).first().click();
    await page.waitForTimeout(400);

    check("拖拽区存在", await page.getByText("拖入文件或整个文件夹").isVisible());
    check("三个入口都在", 
      (await page.getByRole("button", { name: "选择文件", exact: true }).isVisible()) &&
      (await page.getByRole("button", { name: "选择文件夹", exact: true }).isVisible()) &&
      (await page.getByRole("button", { name: "选 ZIP 包", exact: true }).isVisible()));

    // 选目录：webkitdirectory 的 input 传目录路径
    const dirInput = page.locator("input[webkitdirectory]");
    await dirInput.setInputFiles(DIR);
    await page.waitForTimeout(600);

    const summary = await page.getByText(/已选/).first().innerText().catch(() => "");
    check("目录内文件被识别", /已选\s*3\s*个文件/.test(summary), summary);
    check("不支持格式被提前拦掉", /1 个将跳过/.test(summary), summary);
    check("保留相对路径", (await page.getByText("产品/接口/规范.md").count()) > 0);

    await page.getByRole("button", { name: "开始导入" }).click();
    await page.waitForTimeout(1500);
    await page.getByText(/完成：成功 3/).waitFor({ timeout: 120000 });
    check("导入完成且成功 3 篇", true);

    const listText = await page.locator("text=/产品\\/需求\\.md/").first().innerText().catch(() => "");
    check("文档列表出现目录名", listText.includes("产品/需求.md"), listText);

    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.getByText(kbName, { exact: false }).first().click();
    await page.waitForTimeout(600);
    const afterReload = await page.getByText("制度/报销.txt").count();
    check("刷新后仍在（已落库）", afterReload > 0);

    check("无前端报错", errors.length === 0, errors.slice(0, 3).join(" | "));
  } finally {
    await browser.close();
    await api(`/api/kb/${kb.id}`, { method: "DELETE" }, token);
  }

  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n===== ${results.length - failed} / ${results.length} 通过`);
  process.exit(failed ? 1 : 0);
})();
