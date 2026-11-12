import { useCallback, useRef, useState } from "react";
import { FileText } from "lucide-react";

import { cn } from "@/lib/utils";
import { DeckCraftFrame, type DeckCraftHandle, type DeckCraftSaveEvent } from "./DeckCraftFrame";
import { deckFileName } from "./deck-url";
import { takeHandoff } from "./handoff";

const DEFAULT_DECK = "/samples/deck.pptx";

/**
 * 首次进入时解析要打开的演示：`?deck=` 优先（手工传参/旧链接），其次是一次性交接。
 *
 * 结果在模块级缓存：`takeHandoff()` 有副作用（读到即删），而 React 严格模式会把
 * `useState` 的初始化函数调用两次，直接写在那里会让第二次拿到空结果。
 */
let cachedInitial: { url: string; name: string } | null = null;

function initialDeck(): { url: string; name: string } {
  if (cachedInitial) return cachedInitial;
  const q = new URLSearchParams(window.location.search).get("deck");
  const handoff = q ? null : takeHandoff();
  const url = q || handoff?.url || DEFAULT_DECK;
  cachedInitial = { url, name: handoff?.name || deckFileName(url) };
  return cachedInitial;
}

/**
 * DeckCraft（Rust/wasm）编辑页：真母版/版式、动画、放映模式都在这一侧。
 *
 * 这是产品里唯一的独立幻灯片编辑入口（`/deckcraft`）；产物浏览里选中 pptx 时
 * 走的也是同一个引擎（DeckCraftStage），只是保存回写项目文件而不下载。
 * 保存走编辑器内 Ctrl+S，宿主收到 deckcraft:save 后自动下载并记录。
 *
 * 从产物浏览「整页打开」过来时，演示地址经 sessionStorage 交接（见 handoff.ts），
 * 所以地址栏只有 `/deckcraft`，不会挂着一长串 `download?path=…&ticket=…`。
 */
export function DeckCraftPage() {
  const frameRef = useRef<DeckCraftHandle | null>(null);
  const [initial] = useState(initialDeck);
  const [deckUrl, setDeckUrl] = useState(initial.url);
  const [name, setName] = useState(initial.name);
  const [input, setInput] = useState(initial.url);
  const [editing, setEditing] = useState(false);
  const [ready, setReady] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState<DeckCraftSaveEvent | null>(null);
  const [lines, setLines] = useState<string[]>([]);

  const log = useCallback((line: string) => {
    setLines((prev) => [...prev.slice(-7), line]);
  }, []);

  const reload = useCallback(() => {
    const next = input.trim();
    if (!next) return;
    setReady(null);
    setDirty(false);
    setSaved(null);
    setDeckUrl(next);
    setName(deckFileName(next));
    setEditing(false);
  }, [input]);

  const handleSave = useCallback((event: DeckCraftSaveEvent) => {
    setSaved(event);
    setDirty(false);
    // 保存回传的是 pptx 字节：就地下载一份，证明字节是完整可打开的
    const blob = new Blob([event.bytes], { type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = event.name;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
        <div className="min-w-0">
          <h1 className="text-[15px] font-medium text-primary">
            幻灯片编辑（DeckCraft）
            <span className="ml-2 rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-tertiary">
              wasm 自编译 · 中文内置
            </span>
          </h1>
          <p className="mt-0.5 text-[12.5px] text-secondary">
            真母版与版式、动画、放映模式；编辑器内 Ctrl+S 保存，字节回传后自动下载
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {editing ? (
            <input
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") reload();
                if (e.key === "Escape") {
                  setInput(deckUrl);
                  setEditing(false);
                }
              }}
              spellCheck={false}
              className="w-[320px] rounded-md border border-line bg-surface-1 px-2.5 py-1.5 font-mono text-[12px] text-primary outline-none focus:border-line-strong"
              placeholder="pptx 地址，如 /samples/deck.pptx"
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="flex max-w-[260px] items-center gap-1.5 rounded-md border border-line bg-surface-1 px-2.5 py-1.5 text-[12.5px] text-primary transition-colors hover:bg-surface-2"
              title="点击更换演示文件"
            >
              <FileText size={12} className="shrink-0 text-tertiary" />
              <span className="truncate">{name}</span>
            </button>
          )}
          {editing && (
            <button
              onClick={reload}
              className="rounded-md border border-line bg-surface-1 px-3 py-1.5 text-[12.5px] text-primary transition-colors hover:bg-surface-2"
            >
              载入
            </button>
          )}
          <button
            onClick={() => void frameRef.current?.open(deckUrl)}
            className="rounded-md border border-line bg-surface-1 px-3 py-1.5 text-[12.5px] text-primary transition-colors hover:bg-surface-2"
          >
            重新投喂
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <DeckCraftFrame
          ref={frameRef}
          deckUrl={deckUrl}
          onReady={(v) => setReady(v)}
          onDirty={setDirty}
          onSave={handleSave}
          onLog={log}
        />
      </div>

      <footer className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-5 py-2 text-[12px] text-secondary">
        <span className={cn(ready ? "text-primary" : "text-tertiary")}>
          {ready ? `引擎 ${ready}` : "引擎加载中（首次需编译 wasm，约 53MB）"}
        </span>
        <span className={cn("font-mono", dirty ? "text-warning" : "text-tertiary")}>
          {dirty ? "有未保存改动（Ctrl+S 保存）" : "无改动"}
        </span>
        {saved && (
          <span className="text-brand-ink">
            已回收 {saved.name}（{(saved.bytes.byteLength / 1024).toFixed(0)}KB
            {saved.bytes.byteLength > 2
              ? `，PK=${new Uint8Array(saved.bytes.slice(0, 2))[0] === 0x50 ? "✓" : "✗"}`
              : ""}）
          </span>
        )}
        <span className="ml-auto max-w-[520px] truncate font-mono text-[11px] text-tertiary" title={lines.join(" · ")}>
          {lines.join(" · ")}
        </span>
      </footer>
    </div>
  );
}
