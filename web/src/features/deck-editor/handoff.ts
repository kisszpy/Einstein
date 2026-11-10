/**
 * 「整页打开」的跨窗口交接。
 *
 * 早先的做法是把整条下载地址塞进 query（`/deckcraft?deck=<API URL>`），后果是：
 *   - 地址栏和鼠标悬停提示里挂着一长串 `download?path=…&ticket=…`，很丑；
 *   - 票据本身有有效期，写进地址栏意味着刷新/分享都会失效，还会留在浏览器历史里。
 *
 * 现在用 sessionStorage 做一次性交接：同源 `window.open` 打开的新标签页会复制一份
 * opener 的 sessionStorage，新页读得到，而地址栏保持干净的 `/deckcraft`。
 *
 * 为什么用 60 秒有效期而不是判断 `window.opener`：站点一旦设了
 * `Cross-Origin-Opener-Policy`，新页的 `opener` 就是 null，靠它判断会让交接静默失效。
 * 时间窗同样能挡住「新窗口被拦截后残留，之后从侧栏进来误用上次文件」的情况。
 */

const KEY = "deckcraft:handoff";

/** 交接只在刚点过「整页打开」的一小段时间内有效 */
const TTL_MS = 60_000;

export interface DeckHandoff {
  /** 演示的可取地址（可能是带票据的下载地址） */
  url: string;
  /** 展示用文件名，缺省时由地址推断 */
  name?: string;
}

/** 写交接内容。sessionStorage 不可用（隐私模式等）时返回 false，由调用方降级。 */
export function stageHandoff(payload: DeckHandoff): boolean {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...payload, at: Date.now() }));
    return true;
  } catch {
    return false;
  }
}

/** 读取并清除交接内容；没有或已过期时返回 null。 */
export function takeHandoff(): DeckHandoff | null {
  if (typeof window === "undefined") return null;

  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
  } catch {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<DeckHandoff> & { at?: number };
    if (typeof parsed.url !== "string" || !parsed.url) return null;
    if (typeof parsed.at !== "number" || Date.now() - parsed.at > TTL_MS) return null;
    return {
      url: parsed.url,
      name: typeof parsed.name === "string" && parsed.name ? parsed.name : undefined,
    };
  } catch {
    return null;
  }
}
