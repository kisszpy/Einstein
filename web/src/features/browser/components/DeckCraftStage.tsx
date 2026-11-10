/**
 * 放映级编辑台：在产物浏览里直接呈现 DeckCraft（Rust/wasm）。
 *
 * 与同目录的 DeckEditor（结构化 deck + 服务端重排版）是两条互补的路：
 *   - DeckEditor：改文字/版式，保存后由服务端重新生成 pptx，版式不会错位；
 *   - 本组件：真母版、占位符继承、动画与放映模式，保存回传的是完整 pptx 字节。
 * 产物是 pptx 时默认走这一条，用户想看结构化内容再切回去。
 *
 * 保存链路：编辑器内 Ctrl+S → `deckcraft:save` → base64 → PUT /file-bytes → 覆盖项目里的
 * 同一个 pptx。写回后 onSaved 让上层刷新产物列表（文件大小会变）。
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  DeckCraftFrame,
  type DeckCraftHandle,
  type DeckCraftSaveEvent,
} from "@/features/deck-editor/DeckCraftFrame";
import { deckFileName } from "@/features/deck-editor/deck-url";
import { stageHandoff } from "@/features/deck-editor/handoff";
import * as api from "../api";

interface Props {
  project: string;
  path: string;
  /** 预览/下载票据：pptx 字节走同源 download 端点，iframe 里的 fetch 带不了 Authorization */
  ticket: string;
  onSaved?: () => void;
}

type Status =
  | { kind: "idle" }
  | { kind: "saving"; text: string }
  | { kind: "ok"; text: string }
  | { kind: "error"; text: string };

export function DeckCraftStage({ project, path, ticket, onSaved }: Props) {
  const frameRef = useRef<DeckCraftHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [log, setLog] = useState<string[]>([]);

  const deckUrl = useMemo(() => api.downloadUrl(project, path, ticket), [project, path, ticket]);

  const handleSave = useCallback(
    async (event: DeckCraftSaveEvent) => {
      const kb = (event.bytes.byteLength / 1024).toFixed(0);
      setStatus({ kind: "saving", text: `写回中（${kb}KB）…` });
      try {
        const res = await api.saveFileBytes(project, path, event.bytes);
        setDirty(false);
        const delta =
          res.previous_bytes > 0 && res.previous_bytes !== res.bytes
            ? `，原 ${(res.previous_bytes / 1024).toFixed(0)}KB`
            : "";
        setStatus({ kind: "ok", text: `已写回 ${path}（${(res.bytes / 1024).toFixed(0)}KB${delta}）` });
        onSaved?.();
      } catch (err) {
        // 写回失败不能让用户以为存住了：字节还在手里，先提供下载兜底
        setStatus({ kind: "error", text: `写回失败：${(err as Error).message}` });
        download(event);
      }
    },
    [project, path, onSaved],
  );

  const handleReady = useCallback(() => setReady(true), []);
  const handleDirty = useCallback((next: boolean) => setDirty(next), []);
  const handleLog = useCallback((line: string) => {
    setLog((prev) => [...prev.slice(-6), line]);
  }, []);
  const handleSaveEvent = useCallback((event: DeckCraftSaveEvent) => void handleSave(event), [handleSave]);

  /** 整页打开：走 sessionStorage 交接，地址栏只留干净的 /deckcraft。 */
  const openFullPage = useCallback(() => {
    if (stageHandoff({ url: deckUrl, name: deckFileName(deckUrl) })) {
      window.open("/deckcraft", "_blank");
      return;
    }
    // sessionStorage 不可用（隐私模式等）时退回 query 传参，功能优先于观感
    window.open(`/deckcraft?deck=${encodeURIComponent(deckUrl)}`, "_blank");
  }, [deckUrl]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#262626]">
      <DeckCraftFrame
        ref={frameRef}
        deckUrl={deckUrl}
        onReady={handleReady}
        onDirty={handleDirty}
        onSave={handleSaveEvent}
        onLog={handleLog}
      />

      <div className="flex shrink-0 items-center gap-3 border-t border-line bg-surface-1 px-3 py-1.5 text-[11.5px] text-secondary">
        <span className={cn("flex items-center gap-1", ready ? "text-primary" : "text-tertiary")}>
          {ready ? (
            "放映级引擎就绪"
          ) : (
            <>
              <Loader2 size={11} className="animate-spin" /> 引擎加载中（wasm 约 53MB，首次较慢）
            </>
          )}
        </span>

        <span className={cn("font-mono", dirty ? "text-warning" : "text-tertiary")}>
          {dirty ? "有未保存改动 · Ctrl+S" : "无改动"}
        </span>

        {status.kind === "saving" && (
          <span className="flex items-center gap-1 text-tertiary">
            <Loader2 size={11} className="animate-spin" /> {status.text}
          </span>
        )}
        {status.kind === "ok" && (
          <span className="flex min-w-0 items-center gap-1 text-brand">
            <CheckCircle2 size={11} className="shrink-0" />
            <span className="truncate">{status.text}</span>
          </span>
        )}
        {status.kind === "error" && (
          <span className="flex min-w-0 items-center gap-1 text-danger">
            <AlertTriangle size={11} className="shrink-0" />
            <span className="truncate">{status.text}</span>
          </span>
        )}

        <span className="ml-auto flex min-w-0 items-center gap-3">
          <span className="max-w-[380px] truncate font-mono text-[11px] text-tertiary" title={log.join(" · ")}>
            {log.length ? log[log.length - 1] : ""}
          </span>
          <button
            type="button"
            onClick={openFullPage}
            className="flex shrink-0 items-center gap-1 text-tertiary transition-colors hover:text-primary"
            title="在新窗口用整页编辑器打开（适合大屏改版式）"
          >
            <Maximize2 size={11} /> 整页打开
          </button>
        </span>
      </div>
    </div>
  );
}

/** 兜底：写回失败时把字节落成本地文件，至少不丢用户的编辑成果。 */
function download(event: DeckCraftSaveEvent): void {
  const blob = new Blob([event.bytes], {
    type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = event.name;
  a.click();
  URL.revokeObjectURL(url);
}
