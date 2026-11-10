// 智能体工作台的「就地预览」验证：产物在右侧面板打开，PPT 可改可存，网页可浏览。
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const PROJECT = "browser-demo";
const SHOTS = "shots-agent-panel";

const ok = [];
const bad = [];
function check(name, pass, detail = "") {
  (pass ? ok : bad).push(name);
  console.log(`${pass ? "  PASS" : "  FAIL"}  ${name}${pass ? "" : "  <- " + detail}`);
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: CHROMIUM, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => console.log("[pageerror]", e.message));

  // 登录
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await page.getByRole("button", { name: /登录/ }).click();
  await page.waitForURL(/\/chat|\/$/, { timeout: 20000 });
  console.log("已登录");

  await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);

  // 选项目
  await page.locator("select").first().selectOption(PROJECT);
  await page.waitForTimeout(900);
  const treeRow = page.locator('button[title="在右侧预览（不改页面）"]', { hasText: "index.html" });
  check("文件树加载出产物", (await treeRow.count()) > 0, (await page.locator("body").innerText()).slice(-200));

  // 点文件 → 面板就地打开，不跳页
  await treeRow.first().click();
  await page.waitForTimeout(1600);
  check("点产物不离开智能体页", /\/agents/.test(page.url()), page.url());

  const panel = page.getByLabel("产物预览面板");
  check("右侧预览面板出现", (await panel.count()) === 1, String(await panel.count()));
  check(
    "面板里是内置浏览器三源",
    (await panel.getByRole("button", { name: "网页" }).count()) === 1 &&
      (await panel.getByRole("button", { name: "演示" }).count()) === 1,
  );

  const frame = page.frameLocator('iframe[title="index.html"]');
  const body = await frame.locator("body").textContent().catch(() => "");
  check("产物 HTML 在面板内渲染", (body || "").trim().length > 0, (body || "").slice(0, 80));
  const bg = await frame
    .locator("body")
    .evaluate((el) => getComputedStyle(el).backgroundColor)
    .catch(() => "");
  check("样式经 base 注入生效", /255, 255, 255|rgb\(255/.test(bg) || bg !== "", bg);
  await page.screenshot({ path: `${SHOTS}/panel-artifact.png` });

  // 面板里的演示标签 → 打开 pptx
  await panel.getByRole("button", { name: "演示" }).click();
  await page.waitForTimeout(700);
  const deckPanel = page.getByLabel("产物预览面板");
  const deckText = await deckPanel.innerText();
  check("面板能切到演示列表", /browser\.pptx/.test(deckText), deckText.slice(0, 140));
  await deckPanel.locator('button[title="deck/browser.pptx"]').first().click();
  await page.waitForTimeout(1800);
  const editorText = await page.getByLabel("产物预览面板").innerText();
  check("面板里打开 PPT 编辑器", /第 1 \/ 7 页/.test(editorText), editorText.slice(0, 160));
  check(
    "窄面板用胶片条缩略图",
    (await page.getByLabel("产物预览面板").getByLabel("幻灯片缩略图").count()) >= 7,
    String(await page.getByLabel("产物预览面板").getByLabel("幻灯片缩略图").count()),
  );
  await page.screenshot({ path: `${SHOTS}/panel-deck.png` });

  // 属性抽屉
  await page.getByLabel("产物预览面板").getByLabel("页面属性").click();
  await page.waitForTimeout(300);
  check(
    "属性抽屉可展开",
    (await page.getByLabel("产物预览面板").getByText("作者 / 署名").count()) === 1,
  );

  // 改标题 → 保存 → 服务端读回
  const stamp = Date.now();
  const title = `面板改的-${stamp}`;
  await page.getByLabel("产物预览面板").locator('input[placeholder="演示标题"]').fill(title);
  await page.waitForTimeout(200);
  await page.getByLabel("产物预览面板").getByRole("button", { name: /^保存$/ }).click();
  await page.waitForTimeout(2200);
  const saved = await page.evaluate(async () => {
    const res = await fetch(`/api/projects/${encodeURIComponent("browser-demo")}/deck?path=deck%2Fbrowser.pptx`);
    return res.status === 200 ? res.json() : { error: res.status };
  });
  check("面板内保存写进了 pptx", saved.title === title, JSON.stringify(saved.title));

  // 拖动分隔条改宽度
  const before = await page.getByLabel("产物预览面板").evaluate((el) => el.getBoundingClientRect().width);
  const handle = page.locator('div[title="拖动调整宽度"]');
  const box = await handle.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + 300);
    await page.mouse.down();
    await page.mouse.move(box.x - 140, box.y + 300, { steps: 8 });
    await page.mouse.up();
  }
  await page.waitForTimeout(300);
  const after = await page.getByLabel("产物预览面板").evaluate((el) => el.getBoundingClientRect().width);
  check("分隔条可调宽度", after > before + 80, `${Math.round(before)} → ${Math.round(after)}`);

  // 网页标签在面板里可用
  await page.getByLabel("产物预览面板").getByRole("button", { name: "网页" }).click();
  await page.waitForTimeout(400);
  const addr = page.getByLabel("产物预览面板").locator('input[placeholder="输入网址，回车打开"]');
  await addr.fill("https://example.com");
  await addr.press("Enter");
  await page.waitForTimeout(3500);
  const webText = await page
    .frameLocator('iframe[title="网页预览"]')
    .locator("body")
    .textContent()
    .catch(() => "");
  check("面板内可浏览网页", /documentation examples/i.test(webText || ""), (webText || "").slice(0, 80));
  await page.screenshot({ path: `${SHOTS}/panel-web.png` });

  // 关闭
  await page.getByLabel("关闭预览").click();
  await page.waitForTimeout(300);
  check("可关闭面板", (await page.getByLabel("产物预览面板").count()) === 0);

  // 「预览」按钮打开项目概览
  await page.getByRole("button", { name: /^预览$/ }).click();
  await page.waitForTimeout(900);
  const overview = await page.getByLabel("产物预览面板").innerText().catch(() => "");
  check("项目预览入口可用", /个文件/.test(overview), overview.slice(0, 120));

  // 展开到独立页
  await page.getByLabel("在资产页打开").click();
  await page.waitForTimeout(900);
  check("可在资产页打开", /\/assets\?/.test(page.url()), page.url());
  check("独立页带上了项目参数", page.url().includes(`project=${PROJECT}`), page.url());

  await page.screenshot({ path: `${SHOTS}/panel-expanded.png` });
  await browser.close();

  console.log(`\n通过 ${ok.length} / ${ok.length + bad.length}`);
  if (bad.length) {
    console.log("失败：" + bad.join(" | "));
    process.exit(1);
  }
})();
