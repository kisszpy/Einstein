import { useState } from "react";
import { Link } from "react-router-dom";
import { AppWindow, ChevronDown, Globe } from "lucide-react";
import { Markdown } from "./Markdown";
import { cn } from "@/lib/utils";
import type { ChatMessage, WebState } from "../chat-types";

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** 联网状态条：进行中显示在正文上方，拿到来源后显示在正文下方。 */
function WebPanel({ web }: { web: WebState }) {
  const [open, setOpen] = useState(false);

  if (web.status === "checking" || web.status === "searching") {
    return (
      <div className="mb-2 flex items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-tertiary">
        <Globe size={12} className="shrink-0 animate-pulse text-brand" />
        <span className="truncate">
          {web.status === "checking" ? "判断是否需要联网…" : `联网检索：${web.query ?? ""}`}
        </span>
      </div>
    );
  }

  if (web.status === "failed") {
    return (
      <div className="mt-2 flex items-start gap-1.5 text-xs text-tertiary">
        <Globe size={12} className="mt-0.5 shrink-0" />
        <span>联网未取得有效资料{web.message ? `：${web.message}` : ""}</span>
      </div>
    );
  }

  if (web.status !== "done" || !web.sources?.length) return null;

  return (
    <div className="mt-2 border-t border-line pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-tertiary transition-colors hover:text-secondary"
      >
        <Globe size={12} />
        <span>
          已联网 · {web.sources.length} 个来源
          {web.elapsed_ms && web.elapsed_ms >= 500
            ? ` · ${(web.elapsed_ms / 1000).toFixed(1)}s`
            : ""}
        </span>
        <ChevronDown size={12} className={cn("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1">
          {web.sources.map((s) => (
            <li key={s.index} className="flex items-center gap-1.5 text-xs leading-relaxed">
              <a
                href={s.url}
                target="_blank"
                rel="noreferrer"
                title={s.snippet}
                className="text-brand hover:underline"
              >
                [W{s.index}] {s.title}
              </a>
              {hostOf(s.url) && <span className="text-tertiary">{hostOf(s.url)}</span>}
              <Link
                to={`/browser?tab=web&url=${encodeURIComponent(s.final_url || s.url)}`}
                title="在内置浏览器中打开"
                className="shrink-0 text-tertiary transition-colors hover:text-brand"
              >
                <AppWindow size={12} />
              </Link>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const webRunning =
    message.web?.status === "checking" || message.web?.status === "searching";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-card px-4 py-2.5",
          isUser ? "bg-brand-soft text-primary" : "bg-surface-1 text-primary shadow-card",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
        ) : (
          <>
            {webRunning && message.web && <WebPanel web={message.web} />}
            {message.reasoning && (
              <div className="mb-2 rounded-lg border border-line bg-surface-2">
                <button
                  type="button"
                  onClick={() => setReasoningOpen((v) => !v)}
                  className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs text-tertiary"
                >
                  <span className={cn("transition-transform", reasoningOpen && "rotate-90")}>&#9656;</span>
                  {message.streaming && !message.content ? "思考中…" : "思考过程"}
                </button>
                {(reasoningOpen || (message.streaming && !message.content)) && (
                  <p className="max-h-40 overflow-y-auto whitespace-pre-wrap border-t border-line px-2.5 py-1.5 text-xs leading-relaxed text-tertiary">
                    {message.reasoning}
                  </p>
                )}
              </div>
            )}
            <Markdown content={message.content} />
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-brand align-text-bottom" />
            )}
            {!webRunning && message.web && <WebPanel web={message.web} />}
            {message.error && (
              <p className="mt-2 rounded-lg bg-danger/10 px-2 py-1 text-xs text-danger">
                {message.error}
              </p>
            )}
            {message.citations && message.citations.length > 0 && !message.streaming && (
              <div className="mt-2 border-t border-line pt-2">
                <p className="mb-1 text-xs text-tertiary">引用来源</p>
                <div className="flex flex-wrap gap-1">
                  {message.citations.map((c) => (
                    <span
                      key={c.chunk_id}
                      title={c.text}
                      className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary"
                    >
                      [{c.index}] {c.document_name} · {c.score.toFixed(2)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
