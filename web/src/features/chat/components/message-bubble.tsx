import { useState } from "react";
import { Link } from "react-router-dom";
import { AppWindow, ChevronDown, Globe, ThumbsDown, ThumbsUp } from "lucide-react";
import { Markdown } from "./Markdown";
import { cn } from "@/lib/utils";
import type { ChatMessage, WebState } from "../chat-types";

interface CitationLocation {
  chunk_id: string;
  document_name: string;
  text: string;
  char_start: number;
  char_end: number;
  before: string;
  after: string;
}

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

export function MessageBubble({
  message,
  conversationId,
  turn = 1,
}: {
  message: ChatMessage;
  /** 反馈上报需要会话归属；历史会话由 ChatPage 传入 */
  conversationId?: string | null;
  /** 会话内的轮次（第几轮问答），随消息序号传入 */
  turn?: number;
}) {
  const isUser = message.role === "user";
  const [reasoningOpen, setReasoningOpen] = useState(false);
  // 引用原文定位：点引用标签展开原文片段（前后各 200 字上下文）
  const [located, setLocated] = useState<CitationLocation | null>(null);
  const [locateError, setLocateError] = useState<string | null>(null);
  // 赞踩反馈：一次一票，可切换方向
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const webRunning =
    message.web?.status === "checking" || message.web?.status === "searching";

  const sendRating = async (next: "up" | "down") => {
    const final = rating === next ? null : next;
    setRating(final);
    if (!final) return;
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating: final,
          conversation_id: conversationId ?? "",
          turn,
        }),
      });
    } catch {
      /* 反馈失败不打扰用户，汇总在管理页可见 */
    }
  };

  const locate = async (chunkId: string) => {
    if (located?.chunk_id === chunkId) {
      setLocated(null);
      return;
    }
    setLocateError(null);
    try {
      const res = await fetch(`/api/kb/citations/${chunkId}`);
      if (!res.ok) throw new Error(await res.text());
      setLocated((await res.json()) as CitationLocation);
    } catch (e) {
      setLocateError(`原文定位失败：${(e as Error).message.slice(0, 80)}`);
    }
  };

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
                <p className="mb-1 text-xs text-tertiary">引用来源（点击定位原文）</p>
                <div className="flex flex-wrap gap-1">
                  {message.citations.map((c) => (
                    <button
                      key={c.chunk_id}
                      title={c.text}
                      onClick={() => void locate(c.chunk_id)}
                      className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary transition-colors duration-150 hover:bg-brand-soft hover:text-brand"
                    >
                      [{c.index}] {c.document_name} · {c.score.toFixed(2)}
                    </button>
                  ))}
                </div>
                {locateError && (
                  <p className="mt-1 text-xs text-danger">{locateError}</p>
                )}
                {located && (
                  <div className="mt-2 rounded-lg bg-surface-3 p-2.5 text-xs leading-relaxed">
                    <p className="mb-1 text-tertiary">
                      原文定位 · 字符 {located.char_start}–{located.char_end}
                    </p>
                    {located.before && <p className="text-tertiary">…{located.before}</p>}
                    <p className="text-primary">{located.text}</p>
                    {located.after && <p className="text-tertiary">{located.after}…</p>}
                    <button
                      onClick={() => setLocated(null)}
                      className="mt-1 text-tertiary transition-colors duration-150 hover:text-secondary"
                    >
                      收起
                    </button>
                  </div>
                )}
              </div>
            )}
            {/* 赞踩反馈：回答结束后出现 */}
            {!isUser && !message.streaming && message.content && !message.error && (
              <div className="mt-1.5 flex items-center gap-1 text-tertiary">
                <button
                  onClick={() => void sendRating("up")}
                  title="有帮助"
                  className={cn(
                    "rounded p-1 transition-colors duration-150 hover:text-brand",
                    rating === "up" && "text-brand",
                  )}
                >
                  <ThumbsUp size={13} />
                </button>
                <button
                  onClick={() => void sendRating("down")}
                  title="有待改进"
                  className={cn(
                    "rounded p-1 transition-colors duration-150 hover:text-danger",
                    rating === "down" && "text-danger",
                  )}
                >
                  <ThumbsDown size={13} />
                </button>
                {rating && <span className="text-xs">已记录反馈</span>}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
