import { memo, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AppWindow,
  ChevronDown,
  Globe,
  Pencil,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { Markdown } from "./Markdown";
import { normalizeCitations } from "../citation-marks";
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

/** 差评快捷原因：点一下就能定位问题，不用让用户从零描述 */
const DOWN_REASONS = ["与问题无关", "答案错误", "引用不对", "太啰嗦", "其他"] as const;

interface EvalSetBrief {
  id: string;
  kb_id: string;
  name: string;
  items: { query: string }[];
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
        <Globe size={12} className="shrink-0 animate-pulse text-brand-deep dark:text-brand"/>
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
                className="text-brand-deep underline-offset-2 hover:underline dark:text-brand"
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

/** 从响应里取后端 detail，取不到就退回状态码，避免把失败吞掉 */
async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    return `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

/** memo：流式时每个 delta 都会让消息列表重渲染，props 不变的气泡直接跳过。
 *  配合 ChatPage 用 useCallback 提供的稳定回调与 delta 合帧才真正生效。 */
export const MessageBubble = memo(function MessageBubble({
  message,
  index,
  conversationId,
  turn = 1,
  userQuery = "",
  busy = false,
  onRegenerate,
  onEdit,
}: {
  message: ChatMessage;
  /** 本条消息在列表中的下标：回调按 index 定位，保持引用稳定 */
  index: number;
  /** 反馈上报需要会话归属；历史会话由 ChatPage 传入 */
  conversationId?: string | null;
  /** 会话内的轮次（第几轮问答），随消息序号传入 */
  turn?: number;
  /** 该轮的用户问题：差评转评测用例时作为 query */
  userQuery?: string;
  /** 正在流式生成：禁用会改变对话结构的操作 */
  busy?: boolean;
  /** 重新生成这条回答（ChatPage 传入稳定引用，内部按 index 定位） */
  onRegenerate?: (index: number) => void;
  /** 编辑这条提问后重发 */
  onEdit?: (index: number) => void;
}) {
  const isUser = message.role === "user";
  const [reasoningOpen, setReasoningOpen] = useState(false);
  // 引用原文定位：点引用标签展开原文片段（前后各 200 字上下文）
  const [located, setLocated] = useState<CitationLocation | null>(null);
  const [locateError, setLocateError] = useState<string | null>(null);
  // 赞踩反馈：一次一票，可切换方向
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  // 差评原因面板：先选原因（或自己写），再提交
  const [downOpen, setDownOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [feedbackState, setFeedbackState] = useState<"idle" | "sending" | "done">("idle");
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  // 差评转评测用例：选评测集 → 追加一条 query
  const [evalSets, setEvalSets] = useState<EvalSetBrief[]>([]);
  const [evalPickOpen, setEvalPickOpen] = useState(false);
  const [evalBusy, setEvalBusy] = useState(false);
  /** 已成功加入评测集：按钮置灰，避免重复追加同一条用例 */
  const [evalAdded, setEvalAdded] = useState(false);
  const [evalNotice, setEvalNotice] = useState<string | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);
  const webRunning =
    message.web?.status === "checking" || message.web?.status === "searching";
  // 引用编号归一化是两个全局正则的全文扫描，流式下每个 delta 都跑一遍太浪费
  const normalized = useMemo(
    () => normalizeCitations(message.content),
    [message.content],
  );

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

  const submitDownReason = async () => {
    setFeedbackState("sending");
    setFeedbackError(null);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating: "down",
          conversation_id: conversationId ?? "",
          turn,
          reason: reason.trim(),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setRating("down");
      setFeedbackState("done");
    } catch (e) {
      setFeedbackState("idle");
      setFeedbackError(`反馈提交失败：${(e as Error).message}`);
    }
  };

  /** 追加到某个评测集；成功/失败都要给用户明确回执 */
  const appendToEvalSet = async (set: EvalSetBrief) => {
    setEvalBusy(true);
    setEvalError(null);
    try {
      const res = await fetch(`/api/eval/sets/${set.id}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: userQuery.trim(), expect_miss: false }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setEvalPickOpen(false);
      setEvalAdded(true);
      setEvalNotice(`已加入评测集「${set.name}」`);
    } catch (e) {
      setEvalError(`加入评测集失败：${(e as Error).message}`);
    } finally {
      setEvalBusy(false);
    }
  };

  /** 打开选集：只有一个就直连，一个都没有就提示去建 */
  const pickEvalSet = async () => {
    setEvalBusy(true);
    setEvalError(null);
    setEvalNotice(null);
    try {
      const res = await fetch("/api/eval/sets");
      if (!res.ok) throw new Error(await readError(res));
      const sets = (await res.json()) as EvalSetBrief[];
      setEvalSets(sets);
      if (sets.length === 0) {
        setEvalNotice("还没有评测集，请先在评测页建一个");
        return;
      }
      if (sets.length === 1) {
        const only = sets[0];
        if (only) await appendToEvalSet(only);
        return;
      }
      setEvalPickOpen(true);
    } catch (e) {
      setEvalError(`读取评测集失败：${(e as Error).message}`);
    } finally {
      setEvalBusy(false);
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
          "group rounded-card px-4 py-2.5",
          // 用户消息是右侧气泡（85% 封顶）；助手回答与底部输入框同宽，左右边缘严格对齐
          isUser ? "max-w-[85%] bg-brand-soft text-primary" : "w-full bg-surface-1 text-primary shadow-card",
        )}
      >
        {isUser ? (
          <>
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
            {/* 悬停出现的操作：把这条提问填回输入框，发送时截断其后的消息 */}
            {onEdit && (
              <div className="mt-1 flex justify-end opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100">
                <button
                  type="button"
                  onClick={() => onEdit(index)}
                  disabled={busy}
                  title="编辑后重发"
                  className="flex items-center gap-1 rounded px-1 py-0.5 text-xs text-tertiary transition-colors hover:text-brand disabled:opacity-50"
                >
                  <Pencil size={11} />
                  编辑
                </button>
              </div>
            )}
          </>
        ) : (
          <>
            {message.superseded && (
              <p className="mb-1.5 text-xs text-tertiary">上一版</p>
            )}
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
            {/* 引用编号归一化随内容变化才重算；Markdown 自身 memo，内容不变不重解析 */}
            <Markdown content={normalized} />
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-brand align-text-bottom" />
            )}
            {!webRunning && message.web && <WebPanel web={message.web} />}
            {message.error && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-danger/10 px-2 py-1.5 text-xs text-danger">
                <span className="min-w-0 flex-1">{message.error}</span>
                {/* 断线/报错时已生成的文本仍在气泡里，提供一键续答而不是从头再来 */}
                {onRegenerate && !busy && (
                  <button
                    type="button"
                    onClick={() => onRegenerate(index)}
                    className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-medium transition-colors duration-150 hover:bg-danger/10"
                  >
                    <RefreshCw size={11} />
                    继续生成
                  </button>
                )}
              </div>
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
            {/* 赞踩 / 重新生成：回答结束后出现 */}
            {!isUser && !message.streaming && message.content && !message.error && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1 text-tertiary">
                <button
                  onClick={() => void sendRating("up")}
                  title="有帮助"
                  className={cn(
                    "rounded p-1 transition-colors duration-150 hover:text-brand",
                    rating === "up" && "text-brand-deep dark:text-brand"
                  )}
                >
                  <ThumbsUp size={13} />
                </button>
                <button
                  onClick={() => setDownOpen((v) => !v)}
                  title="有待改进"
                  className={cn(
                    "rounded p-1 transition-colors duration-150 hover:text-danger",
                    (rating === "down" || downOpen) && "text-danger",
                  )}
                >
                  <ThumbsDown size={13} />
                </button>
                {onRegenerate && (
                  <button
                    type="button"
                    onClick={() => onRegenerate(index)}
                    disabled={busy}
                    title="重新生成一版，旧答案会保留"
                    className="ml-0.5 flex items-center gap-1 rounded px-1.5 py-1 text-xs transition-colors duration-150 hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <RefreshCw size={12} />
                    重新生成
                  </button>
                )}
                {rating === "up" && <span className="text-xs">已记录反馈</span>}
                {feedbackState === "done" && (
                  <span className="text-xs">已记录差评</span>
                )}
              </div>
            )}

            {/* 差评原因：快捷原因 + 自由输入，提交后可一键转评测用例 */}
            {downOpen && !message.streaming && message.content && !message.error && (
              <div className="mt-2 rounded-lg border border-line bg-surface-2 p-2.5">
                {feedbackState === "done" ? (
                  <>
                    <p className="text-xs text-secondary">
                      已记录差评{reason.trim() ? `：${reason.trim()}` : ""}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void pickEvalSet()}
                        disabled={evalBusy || evalAdded || !userQuery.trim()}
                        title={
                          userQuery.trim()
                            ? "把这一轮的问题加进评测集，用来做回归"
                            : "没有找到这一轮的问题"
                        }
                        className="rounded-md border border-line bg-surface-1 px-2.5 py-1 text-xs text-secondary transition-colors duration-150 hover:border-brand hover:text-brand disabled:opacity-50"
                      >
                        {evalBusy ? "处理中…" : "把这个问题加入评测集"}
                      </button>
                      {evalNotice && (
                        <span className={cn("text-xs", evalAdded ? "text-brand-deep dark:text-brand": "text-tertiary")}>
                          {evalNotice}
                        </span>
                      )}
                    </div>
                    {evalPickOpen && (
                      <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                        <p className="text-xs text-tertiary">选择要加入的评测集</p>
                        {evalSets.map((s) => (
                          <button
                            key={s.id}
                            type="button"
                            disabled={evalBusy}
                            onClick={() => void appendToEvalSet(s)}
                            className="flex w-full items-center justify-between rounded-md border border-line bg-surface-1 px-2 py-1.5 text-left text-xs text-secondary transition-colors duration-150 hover:border-brand hover:text-brand disabled:opacity-50"
                          >
                            <span className="truncate">{s.name}</span>
                            <span className="shrink-0 text-tertiary">{s.items.length} 条</span>
                          </button>
                        ))}
                      </div>
                    )}
                    {evalError && <p className="mt-1.5 text-xs text-danger">{evalError}</p>}
                    <button
                      type="button"
                      onClick={() => setDownOpen(false)}
                      className="mt-2 text-xs text-tertiary transition-colors duration-150 hover:text-secondary"
                    >
                      收起
                    </button>
                  </>
                ) : (
                  <>
                    <p className="mb-1.5 text-xs text-tertiary">哪里需要改进？</p>
                    <div className="flex flex-wrap gap-1.5">
                      {DOWN_REASONS.map((r) => (
                        <button
                          key={r}
                          type="button"
                          onClick={() => setReason(reason === r ? "" : r)}
                          className={cn(
                            "rounded-full border px-2.5 py-1 text-xs transition-colors duration-150",
                            reason === r
                              ? "border-danger bg-danger/10 text-danger"
                              : "border-line bg-surface-1 text-secondary hover:border-line-strong",
                          )}
                        >
                          {r}
                        </button>
                      ))}
                    </div>
                    <input
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          void submitDownReason();
                        }
                      }}
                      placeholder="补充说明（选填）"
                      className="mt-2 h-7 w-full rounded-md border border-line bg-surface-1 px-2 text-xs text-primary outline-none placeholder:text-tertiary focus:border-brand"
                    />
                    {feedbackError && <p className="mt-1 text-xs text-danger">{feedbackError}</p>}
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void submitDownReason()}
                        disabled={feedbackState === "sending"}
                        className="rounded-md bg-brand px-2.5 py-1 text-xs text-brand-fg transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
                      >
                        {feedbackState === "sending" ? "提交中…" : "提交"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setDownOpen(false)}
                        className="text-xs text-tertiary transition-colors duration-150 hover:text-secondary"
                      >
                        取消
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
});
