// 资产页（原内置浏览器）的端到端验证：产物预览 / PPT 编辑保存 / 网页代理与阅读模式。
const { chromium } = require("playwright-core");
const fs = require("fs");

const CHROMIUM =
  process.env.CHROMIUM_PATH ||
  "C:\\Users\\Admin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win64\\chrome.exe";
const BASE = "http://localhost:5173";
const PROJECT = "browser-demo";
const SHOTS = "shots";

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

  // 侧栏入口
  await page.getByRole("link", { name: "资产" }).click();
  await page.waitForURL(/\/assets/, { timeout: 10000 });
  await page.waitForTimeout(600);
  check("侧栏入口进入 /assets", page.url().includes("/assets"), page.url());

  const tabs = await page.locator("button", { hasText: /^网页$/ }).count();
  check("三个来源切换可见", tabs > 0);

  // 选项目
  const select = page.getByLabel("切换项目");
  await select.selectOption(PROJECT);
  await page.waitForTimeout(900);
  const treeText = await page.locator("aside").last().innerText();
  check("文件树加载出产物", /index\.html/.test(treeText), treeText.slice(0, 120));
  check("票据已签发", /票据 已签发/.test(await page.locator("body").innerText()));

  // 点 index.html
  await page.locator("aside").last().locator("button", { hasText: "index.html" }).first().click();
  await page.waitForTimeout(1500);
  const frame = page.frameLocator("iframe");
  const heading = await frame.locator("h1").first().innerText().catch(() => "");
  check("产物 HTML 在 iframe 里渲染", heading.length > 0, `h1="${heading}"`);
  const font = await frame
    .locator("body")
    .evaluate((el) => getComputedStyle(el).fontFamily)
    .catch(() => "");
  check("产物样式已生效（base 注入）", /system-ui/i.test(font), font);
  await page.screenshot({ path: `${SHOTS}/browser-artifact.png` });

  // 演示编辑器
  await page.getByRole("button", { name: /^演示$/ }).click();
  await page.waitForTimeout(700);
  await page.locator("aside").last().locator("button", { hasText: "browser.pptx" }).first().click();
  await page.waitForTimeout(1800);
  const editorText = await page.locator("body").innerText();
  check("演示编辑器打开", /第 1 \/ 7 页/.test(editorText), editorText.slice(0, 160));
  const thumbs = await page.locator('[aria-label="幻灯片缩略图"]').count();
  check("缩略图渲染出 7 页", thumbs === 7, String(thumbs));

  // 改标题 → 保存 → 服务端读回
  const stamp = Date.now();
  const newTitle = `标题-${stamp}`;
  await page.locator('input[placeholder="演示标题"]').fill(newTitle);
  await page.waitForTimeout(250);
  await page.getByRole("button", { name: /^保存$/ }).click();
  await page.waitForTimeout(2200);

  const readDeck = () =>
    page.evaluate(async () => {
      const res = await fetch("/api/projects/browser-demo/deck?path=deck/browser.pptx");
      return res.status === 200 ? res.json() : { error: res.status };
    });

  const afterTitle = await readDeck();
  check("保存后服务端标题已更新", afterTitle.title === newTitle, String(afterTitle.title));

  // 画布内直接编辑封面标题
  const canvasTitle = `画布-${stamp}`;
  await page.locator('[contenteditable="true"]').first().click();
  await page.keyboard.press("Control+a");
  await page.keyboard.type(canvasTitle);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: /^保存$/ }).click();
  await page.waitForTimeout(2200);
  const afterCanvas = await readDeck();
  check(
    "画布内改字写进了 pptx",
    afterCanvas.slides?.[0]?.title === canvasTitle,
    JSON.stringify(afterCanvas.slides?.[0]?.title),
  );
  await page.screenshot({ path: `${SHOTS}/browser-deck.png` });

  // 换主题
  await page.getByLabel("主题配色").selectOption("graphite");
  await page.waitForTimeout(400);
  const saveBtn = page.getByRole("button", { name: /^保存$/ });
  if (await saveBtn.count()) {
    await saveBtn.click();
    await page.waitForTimeout(2200);
  }
  const afterTheme = await readDeck();
  check("换主题保存生效", afterTheme.theme === "graphite", String(afterTheme.theme));
  await page.screenshot({ path: `${SHOTS}/browser-deck-theme.png` });

  // 网页浏览
  await page.getByRole("button", { name: /^网页$/ }).click();
  await page.waitForTimeout(500);
  await page.locator('input[placeholder="输入网址，回车打开"]').fill("https://example.com");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(4000);
  const webFrame = page.frameLocator('iframe[title="网页预览"]');
  const webBody = await webFrame.locator("body").textContent().catch(() => "");
  check("网页代理渲染出内容", /documentation examples/i.test(webBody || ""), (webBody || "").slice(0, 100));
  check("地址栏回填地址", (await page.locator('input[placeholder="输入网址，回车打开"]').inputValue()).includes("example.com"));
  await page.screenshot({ path: `${SHOTS}/browser-web.png` });

  // 阅读模式
  await page.getByRole("button", { name: /^阅读$/ }).click();
  await page.waitForTimeout(2500);
  const article = await page.locator("article").innerText().catch(() => "");
  check("阅读模式抽出正文", /documentation examples/i.test(article), article.slice(0, 120));
  await page.screenshot({ path: `${SHOTS}/browser-read.png` });


  // 深链：智能体产物与对话来源直接跳进资产页
  await page.goto(`${BASE}/assets?tab=deck&project=${PROJECT}&path=deck/browser.pptx`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  const deepEditor = await page.locator("body").innerText();
  check("深链直达 PPT 编辑器", /第 1 \/ 7 页/.test(deepEditor), deepEditor.slice(0, 100));

  await page.goto(`${BASE}/assets?tab=web&url=https://example.com`, { waitUntil: "networkidle" });
  await page.waitForTimeout(3500);
  const deepWeb = await page
    .frameLocator('iframe[title="网页预览"]')
    .locator("body")
    .textContent()
    .catch(() => "");
  check("深链直达网页代理", /documentation examples/i.test(deepWeb || ""), (deepWeb || "").slice(0, 80));

  // 旧入口 /browser 改名后仍要能用：带查询参数重定向到 /assets，老书签/老深链不失效
  await page.goto(`${BASE}/browser?tab=web&url=https://example.com`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  check("旧路径 /browser 重定向到 /assets", page.url().includes("/assets?tab=web"), page.url());

  await browser.close();
  console.log(`\n${ok.length} passed, ${bad.length} failed`);
  process.exit(bad.length ? 1 : 0);
})();
