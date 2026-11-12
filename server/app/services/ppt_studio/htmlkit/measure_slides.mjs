// HTML 幻灯片 → 实测几何 + 截图。后端 htmlkit.measure 调用。
//
// 用法：node measure_slides.mjs <job.json>
// job.json: { slides:[{index, html}], outDir, width, height, screenshot:true }
// 产物：<outDir>/layout.json（几何 + 计算样式 + 逐行文本）、<outDir>/slide-NN.png
// stdout 只打印一行摘要 JSON，几何走文件（一份 deck 的几何可能几 MB）。
//
// 为什么逐字取矩形：Range.getClientRects 只能给出「行片段」，混排字号时
// 一行会被拆成多段。逐字取 rect 再按垂直中心聚类，才能拿到真正的行，
// 且能保留每个 run 的字号/字色（正文里的数字强调就靠这个）。
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const jobPath = process.argv[2];
if (!jobPath) {
  console.error("usage: measure_slides.mjs <job.json>");
  process.exit(2);
}
const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
const nodeModules = process.env.EINSTEIN_NODE_MODULES;
if (!nodeModules) {
  console.error("EINSTEIN_NODE_MODULES is not set");
  process.exit(2);
}
const base = nodeModules.endsWith("/") || nodeModules.endsWith("\\") ? nodeModules : `${nodeModules}/`;
const require = createRequire(base);
const { chromium } = require("playwright-core");

const WIDTH = Number(job.width) || 1280;
const HEIGHT = Number(job.height) || 720;
const outDir = job.outDir;
fs.mkdirSync(outDir, { recursive: true });

const launch = { headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"] };
if (process.env.EINSTEIN_CHROME_PATH) launch.executablePath = process.env.EINSTEIN_CHROME_PATH;

// 浏览器里跑的抽取函数（序列化后注入，避免 Node/浏览器作用域混淆）
function extractSlide() {
  const INLINE = new Set(["SPAN", "B", "I", "EM", "STRONG", "BR", "SUB", "SUP", "U", "SMALL", "CODE"]);
  const root = document.querySelector(".slide");
  if (!root) return { error: "no .slide root", nodes: [] };
  const rootRect = root.getBoundingClientRect();
  const originX = rootRect.left;
  const originY = rootRect.top;
  const nodes = [];
  const diagnostics = [];
  let seq = 0;

  const num = (v) => {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : 0;
  };
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left - originX, y: r.top - originY, w: r.width, h: r.height };
  };
  const styleOf = (cs) => ({
    bg: cs.backgroundColor,
    bgImage: cs.backgroundImage,
    color: cs.color,
    radius: [num(cs.borderTopLeftRadius), num(cs.borderTopRightRadius), num(cs.borderBottomRightRadius), num(cs.borderBottomLeftRadius)],
    borderWidth: [num(cs.borderTopWidth), num(cs.borderRightWidth), num(cs.borderBottomWidth), num(cs.borderLeftWidth)],
    borderColor: [cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor],
    borderStyle: [cs.borderTopStyle, cs.borderRightStyle, cs.borderBottomStyle, cs.borderLeftStyle],
    opacity: num(cs.opacity),
    shadow: cs.boxShadow,
    fontFamily: cs.fontFamily,
    fontSize: num(cs.fontSize),
    fontWeight: cs.fontWeight,
    fontStyle: cs.fontStyle,
    letterSpacing: num(cs.letterSpacing),
    lineHeight: num(cs.lineHeight) || num(cs.fontSize) * 1.2,
    textAlign: cs.textAlign,
    padding: [num(cs.paddingTop), num(cs.paddingRight), num(cs.paddingBottom), num(cs.paddingLeft)],
  });

  const isTextBlock = (el) => {
    if (!(el.textContent || "").trim()) return false;
    for (const child of el.querySelectorAll("*")) {
      if (!INLINE.has(child.tagName)) return false;
    }
    return true;
  };

  // 逐字聚类成行；返回 [{ y, h, runs:[{text, fontSize, fontWeight, color, fontFamily, letterSpacing}] }]
  const linesOf = (el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const lines = [];
    let current = null;
    const range = document.createRange();
    const pushChar = (ch, rect, st) => {
      const centerY = rect.height > 0 ? rect.top + rect.height / 2 : (current ? current.anchor : rect.top);
      const tol = Math.max(6, st.lineHeight * 0.6);
      if (!current || Math.abs(centerY - current.anchor) > tol) {
        current = { anchor: centerY, top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, runs: [], maxFont: st.fontSize };
        lines.push(current);
      } else {
        if (rect.height > 0) {
          current.top = Math.min(current.top, rect.top);
          current.bottom = Math.max(current.bottom, rect.bottom);
          current.left = Math.min(current.left, rect.left);
          current.right = Math.max(current.right, rect.right);
        }
        if (st.fontSize > current.maxFont) {
          current.maxFont = st.fontSize;
          current.anchor = centerY;
        }
      }
      const sig = `${st.fontSize}|${st.fontWeight}|${st.color}|${st.fontFamily}|${st.letterSpacing}|${st.fontStyle}`;
      const last = current.runs[current.runs.length - 1];
      if (last && last.sig === sig) last.text += ch;
      else current.runs.push({ sig, text: ch, fontSize: st.fontSize, fontWeight: st.fontWeight, fontStyle: st.fontStyle, color: st.color, fontFamily: st.fontFamily, letterSpacing: st.letterSpacing });
    };
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!parent) continue;
      const cs = getComputedStyle(parent);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      const st = {
        fontSize: num(cs.fontSize), fontWeight: cs.fontWeight, fontStyle: cs.fontStyle,
        color: cs.color, fontFamily: cs.fontFamily, letterSpacing: num(cs.letterSpacing),
        lineHeight: num(cs.lineHeight) || num(cs.fontSize) * 1.2,
      };
      const text = node.nodeValue || "";
      for (let i = 0; i < text.length; i += 1) {
        const ch = text[i];
        range.setStart(node, i);
        range.setEnd(node, i + 1);
        const r = range.getBoundingClientRect();
        if (ch === "\n" || ch === "\r" || ch === "\t") continue;
        pushChar(ch, r, st);
      }
    }
    range.detach?.();
    return lines
      .map((line) => ({
        x: line.left - originX, y: line.top - originY,
        w: Math.max(0, line.right - line.left), h: Math.max(0, line.bottom - line.top),
        runs: line.runs.map((r) => ({
          text: r.text, fontSize: r.fontSize, fontWeight: r.fontWeight, fontStyle: r.fontStyle,
          color: r.color, fontFamily: r.fontFamily, letterSpacing: r.letterSpacing,
        })),
      }))
      .filter((line) => line.runs.some((r) => r.text.trim()));
  };

  const walk = (el, depth) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || num(cs.opacity) === 0) return;
    const rect = rectOf(el);
    const directive = el.getAttribute("data-pptx");
    const id = `n${seq++}`;
    if (directive) {
      const data = {};
      for (const attr of el.attributes) {
        if (attr.name.startsWith("data-") && attr.name !== "data-pptx") data[attr.name.slice(5)] = attr.value;
      }
      nodes.push({ id, kind: "directive", directive, tag: el.tagName.toLowerCase(), rect, style: styleOf(cs), data, depth });
      return; // 指令节点自己负责画，子树（SVG 视觉孪生）不再遍历
    }
    if (el.tagName === "SVG" || el.tagName === "svg") {
      diagnostics.push({ ruleId: "svg-without-directive", detail: "SVG 缺少 data-pptx 指令，pptx 侧会丢图形" });
      return;
    }
    if (isTextBlock(el)) {
      const lines = linesOf(el);
      if (lines.length) {
        const st = styleOf(cs);
        const boxVisible =
          (cs.backgroundColor !== "rgba(0, 0, 0, 0)" && cs.backgroundColor !== "transparent") ||
          cs.backgroundImage !== "none" ||
          st.borderWidth.some((w) => w > 0);
        // 文本块自身也可能有底色/圆角（chip、表头单元格）：形状先入队，文字压在上面
        if (boxVisible) {
          nodes.push({ id: `${id}-box`, kind: "shape", tag: el.tagName.toLowerCase(), className: String(el.className || ""), rect, style: st, depth });
        }
        nodes.push({ id, kind: "text", tag: el.tagName.toLowerCase(), className: String(el.className || ""), rect, style: st, lines, depth });
      }
      return;
    }
    const bgVisible = cs.backgroundColor !== "rgba(0, 0, 0, 0)" && cs.backgroundColor !== "transparent";
    const hasBorder = styleOf(cs).borderWidth.some((w, i) => w > 0 && cs.borderTopStyle !== "none" && styleOf(cs).borderColor[i] !== "rgba(0, 0, 0, 0)");
    if ((bgVisible || cs.backgroundImage !== "none" || hasBorder || cs.boxShadow !== "none") && rect.w > 0 && rect.h > 0) {
      nodes.push({ id, kind: "shape", tag: el.tagName.toLowerCase(), className: String(el.className || ""), rect, style: styleOf(cs), depth });
    }
    const cls = String(el.className || "");
    if (!cls.startsWith("m-") && (rect.w > rootRect.width + 1 || rect.h > rootRect.height + 1)) {
      diagnostics.push({ ruleId: "canvas-overflow", detail: `${cls || el.tagName} 超出画布`, rect });
    }
    for (const child of el.children) walk(child, depth + 1);
  };

  const rootStyle = styleOf(getComputedStyle(root));
  nodes.push({ id: "root", kind: "shape", tag: "div", className: "slide", rect: { x: 0, y: 0, w: rootRect.width, h: rootRect.height }, style: rootStyle, depth: -1 });
  for (const child of root.children) walk(child, 0);

  // 溢出/裁切体检：任何 scroll 尺寸大于 client 尺寸的文本容器都是裁切
  for (const el of root.querySelectorAll("*")) {
    if (!isTextBlock(el)) continue;
    if (el.scrollHeight - el.clientHeight > 2 || el.scrollWidth - el.clientWidth > 2) {
      diagnostics.push({
        ruleId: "text-clipped",
        detail: `${el.className || el.tagName} 内容被裁切（${el.scrollHeight}>${el.clientHeight}）`,
        className: String(el.className || ""),
        text: (el.textContent || "").slice(0, 40),
      });
    }
  }
  return { nodes, diagnostics, size: { w: rootRect.width, h: rootRect.height } };
}

let browser;
const results = [];
try {
  browser = await chromium.launch(launch);
  const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
  for (const slide of job.slides || []) {
    await page.setContent(slide.html, { waitUntil: "load", timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(30);
    const extracted = await page.evaluate(extractSlide);
    let shot = "";
    if (job.screenshot !== false) {
      shot = path.join(outDir, `slide-${String(slide.index).padStart(2, "0")}.png`);
      await page.locator(".slide").screenshot({ path: shot });
    }
    results.push({ index: slide.index, screenshot: shot, ...extracted });
  }
  fs.writeFileSync(path.join(outDir, "layout.json"), JSON.stringify({ slides: results }), "utf8");
  console.log(JSON.stringify({ ok: true, slides: results.length, out: path.join(outDir, "layout.json") }));
} catch (err) {
  console.log(JSON.stringify({ ok: false, error: String((err && err.message) || err) }));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}