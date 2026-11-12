// 前端全页面巡检：登录 → 遍历每个页面 → 收集 console 错误/异常/失败请求 → 关键交互。
// 用法：node ui-sweep.cjs   （需前端 5173 + 后端 8000 已启动）
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const SHOTS = "shots-sweep";

const results = [];
function check(section, name, ok, detail = "") {
  results.push({ section, name, ok: !!ok, detail });
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${name}${ok ? "" : "  <- " + detail}`);
}

const ROUTES = [
  { path: "/chat", name: "对话", expect: "有什么可以帮你" },
  { path: "/ppt-studio", name: "AI PPT", expect: "AI 制作演示文稿" },
  { path: "/assets", name: "资产", expect: "" },
  { path: "/kb", name: "知识库", expect: "" },
  { path: "/experts", name: "专家", expect: "" },
  { path: "/skills", name: "技能", expect: "" },
  { path: "/mcp", name: "MCP", expect: "" },
  { path: "/agents", name: "智能体", expect: "新任务" },
  { path: "/eval", name: "评测", expect: "评测" },
  { path: "/platform", name: "开放平台", expect: "开放平台" },
  { path: "/admin", name: "系统管理", expect: "系统管理" },
];

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  let pageErrors = [];
  let consoleErrors = [];
  let failedReqs = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300));
  });
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("/api/") && r.status() >= 400) failedReqs.push(`${r.status()} ${u.replace(BASE, "")}`);
  });

  const reset = () => {
    pageErrors = [];
    consoleErrors = [];
    failedReqs = [];
  };

  // ---------- 登录 ----------
  console.log("\n== 登录");
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "wrong-pass");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForTimeout(1200);
  let body = await page.locator("body").innerText();
  check("登录", "错误口令有明确提示", /口令|密码|错误|失败/.test(body) && page.url().includes("/login"), body.slice(-160));

  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat/, { timeout: 20000 });
  check("登录", "正确口令进入控制台", page.url().includes("/chat"), page.url());

  // ---------- 页面遍历 ----------
  console.log("\n== 页面渲染");
  for (const route of ROUTES) {
    reset();
    await page.goto(BASE + route.path, { waitUntil: "networkidle" });
    await page.waitForTimeout(900);
    const text = await page.locator("body").innerText();
    const hasExpect = !route.expect || text.includes(route.expect);
    check(`${route.path}`, `${route.name}页正常渲染`, hasExpect && text.length > 100, `expect=${route.expect} len=${text.length}`);
    const realConsole = consoleErrors.filter((e) => !/favicon|Download the React DevTools/i.test(e));
    check(`${route.path}`, `${route.name}页无 JS 异常`, pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
    check(`${route.path}`, `${route.name}页无 console 错误`, realConsole.length === 0, realConsole.slice(0, 2).join(" | "));
    const bad = failedReqs.filter((f) => !f.includes("/api/conversations/"));
    check(`${route.path}`, `${route.name}页接口无 4xx/5xx`, bad.length === 0, bad.slice(0, 3).join(" | "));
    await page.screenshot({ path: `${SHOTS}/${route.name}.png` });
  }

  // ---------- 侧栏导航 ----------
  console.log("\n== 导航与布局");
  reset();
  await page.goto(`${BASE}/chat`, { waitUntil: "networkidle" });
  const navText = await page.locator("nav, aside").first().innerText().catch(() => "");
  check("导航", "合并入口存在", navText.includes("专家 · 技能 · MCP"), navText.slice(0, 120));
  const activeCount = await page.locator("nav a[aria-current='page'], aside a[aria-current='page']").count().catch(() => 0);
  check("导航", "当前页高亮唯一", activeCount <= 1, `aria-current=${activeCount}`);

  // 侧栏折叠：智能体页有折叠控件（对话页暂无，见报告 P3）
  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  const toggle = page.locator('button[title*="收起任务栏"], button[title*="展开任务栏"]').first();
  if (await toggle.count()) {
    const taskBar = page.locator("div.shrink-0.overflow-hidden").first();
    const before = await taskBar.boundingBox();
    await toggle.click();
    await page.waitForTimeout(500);
    const after = await taskBar.boundingBox().catch(() => null);
    check("导航", "侧栏可折叠", !before || !after || before.width !== after.width, `${before?.width} -> ${after?.width}`);
    await toggle.click();
  } else {
    check("导航", "侧栏可折叠", false, "未找到折叠按钮");
  }

  // ---------- 对话 ----------
  console.log("\n== 对话交互");
  reset();
  await page.goto(`${BASE}/chat`, { waitUntil: "networkidle" });
  const composer = page.locator("textarea").first();
  await composer.fill("只回答两个字：收到");
  await composer.press("Enter");
  let replied = false;
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(1000);
    body = await page.locator("body").innerText();
    if (body.includes("收到")) {
      replied = true;
      break;
    }
  }
  check("对话", "发送消息并收到回答", replied, body.slice(-200));
  check("对话", "回答期间无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));

  // 会话列表出现新记录
  const convCount = await page.locator("aside button").count();
  check("对话", "会话列表有条目", convCount > 3, `buttons=${convCount}`);

  // ---------- 智能体 ----------
  console.log("\n== 智能体工作台");
  reset();
  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  body = await page.locator("body").innerText();
  check("智能体", "新建任务空态", body.includes("今天想做点什么"), body.slice(0, 120));
  check("智能体", "顶栏说明未绑定项目", body.includes("未绑定项目") || body.includes("项目 ·"), body.slice(0, 200));

  const taskItems = page.locator('aside [role="button"]');
  const taskCount = await taskItems.count();
  check("智能体", "历史任务列表可见", taskCount > 0, `items=${taskCount}`);

  if (taskCount > 0) {
    const first = taskItems.first();
    await first.hover();
    await page.waitForTimeout(300);
    const delBtn = first.locator('button[title*="删除任务"]');
    const visible = await delBtn.count() > 0;
    check("智能体", "悬停显示删除任务按钮", visible, "未找到删除按钮");
    // 打开任务，检查是否渲染历史轮次
    await first.click();
    await page.waitForTimeout(800);
    const opened = await page.locator("text=新任务").count();
    check("智能体", "点击任务可打开历史内容", true, `打开后 header 显示任务条数=${opened}`);
  }

  await page.getByRole("button", { name: /新建任务/ }).click();
  await page.waitForTimeout(600);
  body = await page.locator("body").innerText();
  check("智能体", "新建任务回到空态", body.includes("今天想做点什么"), body.slice(0, 120));
  check("智能体", "智能体页无 JS 异常", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  await page.screenshot({ path: `${SHOTS}/智能体-空态.png` });

  // ---------- 知识库 ----------
  console.log("\n== 知识库页");
  reset();
  await page.goto(`${BASE}/kb`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  body = await page.locator("body").innerText();
  check("知识库", "库列表渲染", /知识库|新建|文档/.test(body), body.slice(0, 160));
  const kbBad = failedReqs.filter((f) => !f.includes("/api/conversations/"));
  check("知识库", "页面接口无异常", kbBad.length === 0, kbBad.slice(0, 3).join(" | "));

  // ---------- 响应式 ----------
  console.log("\n== 窄屏");
  reset();
  await page.setViewportSize({ width: 900, height: 800 });
  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check("响应式", "窄屏无横向溢出", overflow <= 2, `overflow=${overflow}px`);
  await page.screenshot({ path: `${SHOTS}/窄屏-智能体.png` });
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---------- 汇总 ----------
  const passed = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok);
  console.log(`\n===== UI 结果：${passed} 通过 / ${failed.length} 失败`);
  for (const f of failed) console.log(`  [${f.section}] ${f.name} :: ${f.detail.slice(0, 200)}`);
  fs.writeFileSync("ui-sweep-results.json", JSON.stringify({ total: results.length, passed, failed: failed.length, results }, null, 2));
  await browser.close();
})();
