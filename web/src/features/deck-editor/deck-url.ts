/**
 * 从演示地址里取出「能给人看」的文件名。
 *
 * 我们喂给编辑器的地址有两种：
 *   1. `/api/projects/<项目>/download?path=deck.pptx&ticket=…`（浏览页的产物）
 *   2. `/samples/deck.pptx`（内置样本 / 用户手填）
 *
 * 直接 `url.split("/").pop()` 在第一种情况下会得到
 * `download?path=deck.pptx&ticket=eyJ…` —— 这串东西会出现在编辑器标题、另存文件名和
 * 日志里。这里统一取 `path` 参数，没有就取最后一段路径。
 */
export function deckFileName(url: string): string {
  const fallback = "演示文稿.pptx";
  try {
    const base = typeof window !== "undefined" ? window.location.origin : "http://localhost";
    const parsed = new URL(url, base);
    const path = parsed.searchParams.get("path") ?? parsed.pathname;
    const tail = path.split("/").filter(Boolean).pop();
    if (!tail) return fallback;
    const decoded = decodeURIComponent(tail).trim();
    return decoded || fallback;
  } catch {
    return fallback;
  }
}
