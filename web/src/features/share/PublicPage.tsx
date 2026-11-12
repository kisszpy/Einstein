import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ArrowUp, Link2 } from "lucide-react";
import { Markdown } from "@/features/chat/components/Markdown";
import { normalizeCitations } from "@/features/chat/citation-marks";
import type { PublicShare } from "./api";

interface Citation {
  index?: number;
  document_name: string;
  text: string;
}

/**
 * 免登录只读页。
 *
 * 这个页面完全在鉴权之外，所以只认 URL 里的 token，不带任何"顺手取一下"
 * 的请求——它同时也是 iframe 嵌入的落点，任何一次多余请求都会在别人的
 * 站点上变成一次 401。
 */
export function PublicPage() {
  const { token = "" } = useParams();
  const [data, setData] = useState<PublicShare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string>("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const res = await fetch(`/api/public/shares/${token}`);
      if (!alive) return;
      if (!res.ok) {
        setError("链接已失效或不存在");
        return;
      }
      setData(await res.json());
    })();
    return () => {
      alive = false;
    };
  }, [token]);

  const ask = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setAskError(null);
    setAnswer("");
    setCitations([]);
    try {
      const res = await fetch(`/api/public/shares/${token}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "提问失败");
      }
      const body = await res.json();
      setAnswer(body.answer || "");
      setCitations(body.citations || []);
    } catch (e) {
      setAskError(e instanceof Error ? e.message : "提问失败");
    } finally {
      setAsking(false);
    }
  };

  if (error) {
    return (
      <Shell>
        <div className="py-20 text-center">
          <p className="text-sm text-secondary">{error}</p>
          <p className="mt-2 text-xs text-tertiary">
            链接可能已被撤销或超过有效期，请向分享者索要新的链接。
          </p>
        </div>
      </Shell>
    );
  }

  if (!data) {
    return (
      <Shell>
        <p className="py-20 text-center text-sm text-tertiary">加载中…</p>
      </Shell>
    );
  }

  const conv = data.conversation;
  const kb = data.kb;

  return (
    <Shell>
      <div className="flex items-center gap-2 border-b border-line pb-3">
        <Link2 size={14} className="text-brand-ink "/>
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-medium">
          {conv?.title || kb?.name || data.title}
        </h1>
        <span className="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[12px] text-brand-ink ">
          只读分享
        </span>
      </div>

      {kb && (
        <div className="space-y-4 pt-4">
          {kb.description && <p className="text-[13px] text-secondary">{kb.description}</p>}
          {!data.allow_ask && (
            <p className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-xs text-tertiary">
              分享者未开启提问，仅展示知识库说明。
            </p>
          )}
          {data.allow_ask && (
            <>
              <div className="rounded-xl border border-line bg-surface-1 p-3">
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void ask();
                    }
                  }}
                  rows={3}
                  placeholder="就这个知识库里的内容提问…"
                  className="w-full resize-none bg-transparent text-[13px] leading-relaxed outline-none placeholder:text-tertiary"
                />
                <div className="flex justify-end">
                  <button
                    onClick={() => void ask()}
                    disabled={asking || !question.trim()}
                    className="flex h-8 items-center gap-1.5 rounded-lg bg-brand px-3 text-xs text-brand-fg disabled:opacity-40"
                  >
                    {asking ? "思考中…" : "提问"}
                    <ArrowUp size={13} />
                  </button>
                </div>
              </div>
              {askError && <p className="text-xs text-danger">{askError}</p>}
              {answer && (
                <div className="space-y-3">
                  <div className="rounded-xl border border-line bg-surface-1 p-4">
                    <Markdown content={normalizeCitations(answer)} />
                  </div>
                  {citations.length > 0 && (
                    <div className="space-y-1.5">
                      <p className="text-[12px] text-tertiary">引用资料</p>
                      {citations.map((c, i) => (
                        <div
                          key={`${c.document_name}-${i}`}
                          className="rounded-lg border border-line px-3 py-2 text-[12px] text-secondary"
                        >
                          <span className="mr-1 text-brand-ink ">[{c.index ?? i + 1}]</span>
                          {c.document_name}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {conv && (
        <div className="space-y-4 pt-4">
          {conv.messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[85%] rounded-xl bg-brand-soft px-3.5 py-2 text-[13px] leading-relaxed"
                    : "max-w-full rounded-xl border border-line bg-surface-1 px-4 py-3 text-[13px]"
                }
              >
                {m.role === "user" ? (
                  <p className="whitespace-pre-wrap">{m.content}</p>
                ) : (
                  <Markdown content={normalizeCitations(m.content)} />
                )}
              </div>
            </div>
          ))}
          {conv.messages.length === 0 && (
            <p className="py-10 text-center text-xs text-tertiary">这段对话没有消息</p>
          )}
        </div>
      )}

      <p className="mt-8 border-t border-line pt-3 text-center text-[12px] text-tertiary">
        由 Einstein 分享 · 内容仅供参考
      </p>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-page px-4 py-8">
      <div className="mx-auto w-full max-w-[760px] rounded-2xl border border-line bg-surface-2 p-6 shadow-sm">
        {children}
      </div>
    </div>
  );
}
