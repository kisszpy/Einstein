/**
 * 内置网页浏览。
 *
 * iframe 指向服务端代理（去掉 frame 限制、补 base），站内链接由代理注入的
 * 导航桥用 postMessage 上报，这里接管后重新走代理——否则直接跳原站会被
 * `X-Frame-Options` 拦住变白屏。
 */

import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  ChevronRight,
  ExternalLink,
  Globe,
  Loader2,
  RotateCw,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import * as api from "./api";
import type { ReadPayload } from "./types";

const SHORTCUTS = [
  { label: "百度", url: "https://www.baidu.com" },
  { label: "Bing", url: "https://cn.bing.com" },
  { label: "知乎", url: "https://www.zhihu.com" },
  { label: "GitHub", url: "https://github.com" },
  { label: "MDN", url: "https://developer.mozilla.org/zh-CN/" },
  { label: "arXiv", url: "https://arxiv.org" },
];

interface Props {
  url: string;
  ticket: string;
  onNavigate: (url: string) => void;
}

export function WebView({ url, ticket, onNavigate }: Props) {
  const [entry, setEntry] = useState(url);
  const [draft, setDraft] = useState(url);
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");
  const [readMode, setReadMode] = useState(false);
  const [article, setArticle] = useState<ReadPayload | null>(null);
  const [readError, setReadError] = useState("");
  const [readLoading, setReadLoading] = useState(false);

  const [stack, setStack] = useState<string[]>(url ? [url] : []);
  const [cursor, setCursor] = useState(0);

  const go = useCallback(
    (next: string, mode: "push" | "replace" | "none" = "push") => {
      if (!next) return;
      setEntry(next);
      setDraft(next);
      setNotice("");
      setReady(false);
      setLoading(true);
      setReadMode(false);
      setArticle(null);
      if (mode === "push") {
        const trimmed = [...stack.slice(0, cursor + 1), next];
        setStack(trimmed);
        setCursor(trimmed.length - 1);
      }
      if (mode !== "none") onNavigate(next);
    },
    [cursor, stack, onNavigate],
  );

  // 外部跳进来（侧栏入口、对话来源）时重置历史
  useEffect(() => {
    if (!url || url === entry) return;
    setStack([url]);
    setCursor(0);
    setEntry(url);
    setDraft(url);
    setLoading(true);
    setReady(false);
    setReadMode(false);
    setArticle(null);
  }, [url, entry]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { source?: string; type?: string; url?: string; message?: string };
      if (!data || data.source !== "einstein-browser") return;
      if (data.type === "ready") {
        setReady(true);
        setLoading(false);
      }
      if (data.type === "navigate" && data.url) go(data.url);
      if (data.type === "notice" && data.message) setNotice(data.message);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [go]);

  // 导航桥没报到就说明页面被拦或不是 HTML，给个明确出口
  useEffect(() => {
    if (!loading) return;
    const timer = window.setTimeout(() => {
      setNotice((prev) => prev || "页面没有正常加载，可能被目标站拦截，可试试阅读模式");
      setLoading(false);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [loading, entry, reloadKey]);

  const loadArticle = async () => {
    setReadLoading(true);
    setReadError("");
    try {
      const data = await api.readWebPage(entry);
      if (!data.text) {
        setReadError(
          data.kind === "pdf" ? "PDF 没法在这里排版成正文，用系统浏览器打开更合适" : "这个页面没有可读正文",
        );
      }
      setArticle(data);
      setReadMode(true);
    } catch (exc) {
      setReadError((exc as Error).message);
      setReadMode(true);
    } finally {
      setReadLoading(false);
    }
  };

  const canBack = cursor > 0;
  const canForward = cursor < stack.length - 1;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 地址栏 */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-line px-2.5 py-2">
        <button
          onClick={() => {
            if (!canBack) return;
            const target = stack[cursor - 1];
            if (!target) return;
            setCursor(cursor - 1);
            go(target, "none");
          }}
          disabled={!canBack}
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
          title="后退"
        >
          <ArrowLeft size={14} />
        </button>
        <button
          onClick={() => {
            if (!canForward) return;
            const target = stack[cursor + 1];
            if (!target) return;
            setCursor(cursor + 1);
            go(target, "none");
          }}
          disabled={!canForward}
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3 disabled:opacity-30"
          title="前进"
        >
          <ArrowRight size={14} />
        </button>
        <button
          onClick={() => {
            setLoading(true);
            setReady(false);
            setReloadKey((n) => n + 1);
          }}
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
          title="刷新"
        >
          <RotateCw size={13} className={cn(loading && "animate-spin")} />
        </button>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            const next = draft.trim();
            if (!next) return;
            go(/^https?:\/\//i.test(next) ? next : `https://${next}`);
          }}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-line bg-surface-2 px-2.5 py-1 focus-within:border-brand"
        >
          <Globe size={13} className="shrink-0 text-tertiary" />
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="输入网址，回车打开"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-tertiary"
          />
          {loading && <Loader2 size={12} className="animate-spin text-tertiary" />}
        </form>

        <button
          onClick={loadArticle}
          disabled={readLoading}
          className={cn(
            "flex items-center gap-1.5 rounded-md border border-line px-2 py-1.5 text-[11.5px]",
            readMode ? "bg-brand-soft text-brand" : "text-secondary hover:bg-surface-3",
          )}
          title="抽取正文，不受框架限制"
        >
          {readLoading ? <Loader2 size={12} className="animate-spin" /> : <BookOpen size={12} />}
          阅读
        </button>
        <a
          href={entry}
          target="_blank"
          rel="noreferrer"
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
          title="系统浏览器打开"
        >
          <ExternalLink size={13} />
        </a>
      </div>

      {notice && (
        <div className="flex shrink-0 items-center gap-1.5 border-b border-warning/30 bg-warning/10 px-3 py-1.5 text-[11.5px] text-warning">
          <TriangleAlert size={12} /> {notice}
          <button
            onClick={loadArticle}
            className="ml-auto rounded px-1.5 py-0.5 underline-offset-2 hover:underline"
          >
            切阅读模式
          </button>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {!entry ? (
          <StartPage onPick={(next) => go(next)} />
        ) : readMode ? (
          <div className="h-full overflow-auto bg-surface-1">
            <article className="mx-auto max-w-[720px] px-8 py-8">
              <h1 className="text-[19px] font-medium leading-snug">
                {article?.title || entry}
              </h1>
              <p className="mt-1 truncate text-[11.5px] text-tertiary">{article?.final_url || entry}</p>
              {readError ? (
                <p className="mt-6 text-[13px] text-danger">{readError}</p>
              ) : (
                <div className="mt-6 space-y-3 text-[13.5px] leading-[1.85] text-secondary">
                  {(article?.text || "")
                    .split(/\n{1,}/)
                    .map((line) => line.trim())
                    .filter(Boolean)
                    .map((line, i) => <p key={i}>{line}</p>)}
                </div>
              )}
            </article>
          </div>
        ) : (
          <iframe
            key={`${entry}-${reloadKey}`}
            src={api.proxyUrl(entry, ticket)}
            title="内置浏览器"
            className="h-full w-full border-0 bg-white"
            sandbox="allow-scripts allow-forms allow-popups"
          />
        )}

        {entry && !readMode && !ready && !loading && (
          <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
            <span className="pointer-events-auto rounded-full border border-line bg-surface-1/95 px-3 py-1 text-[11px] text-tertiary shadow-[0_2px_8px_rgb(0_0_0/0.08)]">
              页面可能只加载了部分内容 · 可切换阅读模式
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function StartPage({ onPick }: { onPick: (url: string) => void }) {
  return (
    <div className="h-full overflow-y-auto px-6 py-10">
      <div className="mx-auto max-w-[620px]">
        <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-soft text-brand">
          <Globe size={19} strokeWidth={1.7} />
        </span>
        <h2 className="mt-4 text-[17px] font-medium">内置浏览器</h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-tertiary">
          页面经服务端代理取回，所以那些禁止被嵌入的站点也能在这里打开。站内点击会留在窗口内，
          被拦得厉害时切「阅读」只取正文。
        </p>
        <div className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {SHORTCUTS.map((item) => (
            <button
              key={item.url}
              onClick={() => onPick(item.url)}
              className="flex items-center gap-2 rounded-lg border border-line bg-surface-1 px-3 py-2.5 text-left text-[12.5px] hover:border-brand/40 hover:bg-brand-soft/30"
            >
              <Globe size={13} className="shrink-0 text-tertiary" />
              <span className="flex-1 truncate">{item.label}</span>
              <ChevronRight size={12} className="shrink-0 text-tertiary" />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
