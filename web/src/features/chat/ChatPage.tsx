import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, Globe, Pin, PinOff, Search, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  ComposerFrame,
  ComposerInput,
  ComposerSendButton,
  ComposerToolbar,
} from "@/components/ui/composer";
import { useChatStream } from "./use-chat-stream";
import { MessageBubble } from "./components/message-bubble";
import type { WebMode } from "./chat-types";
import { cn } from "@/lib/utils";

const WEB_KEY = "einstein.chat.webMode";
const WEB_LABEL: Record<WebMode, string> = { auto: "自动", on: "始终", off: "关闭" };
const WEB_HINT: Record<WebMode, string> = {
  auto: "需要最新信息时才联网",
  on: "本轮强制联网检索",
  off: "本轮只用已有知识",
};
const WEB_NEXT: Record<WebMode, WebMode> = { auto: "on", on: "off", off: "auto" };

function readWebMode(): WebMode {
  const saved = localStorage.getItem(WEB_KEY);
  return saved === "on" || saved === "off" ? saved : "auto";
}

interface ExpertItem {
  id: string;
  name: string;
  emoji: string;
  description: string;
  kb_ids: string[];
  greeting?: string;
  suggestions?: string[];
}

interface ConversationItem {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
  archived?: boolean;
  pinned?: boolean;
}

export function ChatPage() {
  const { messages, isStreaming, conversationId, send, stop, reset, loadConversation } =
    useChatStream();
  const [input, setInput] = useState("");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<ConversationItem[]>([]);
  const [experts, setExperts] = useState<ExpertItem[]>([]);
  const [expertId, setExpertId] = useState<string | null>(null);
  const [webMode, setWebMode] = useState<WebMode>(readWebMode);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(WEB_KEY, webMode);
  }, [webMode]);

  const loadList = useCallback(async (q = "") => {
    const url = q.trim()
      ? `/api/conversations?q=${encodeURIComponent(q.trim())}`
      : "/api/conversations";
    const res = await fetch(url);
    setConversations(await res.json());
  }, []);

  const loadArchived = useCallback(async (visible: boolean) => {
    if (!visible) {
      setArchived([]);
      return;
    }
    const res = await fetch("/api/conversations?archived=true");
    setArchived(await res.json());
  }, []);

  useEffect(() => {
    void loadList();
    void loadArchived(showArchived);
    // 对话页只展示发布流 status=live 的专家；草稿与测试中的在专家管理页可见
    void fetch("/api/experts?published=true")
      .then((r) => r.json())
      .then(setExperts);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadList, loadArchived]);

  // 搜索防抖：停顿 300ms 后请求服务端全文搜索
  useEffect(() => {
    const timer = window.setTimeout(() => void loadList(searchQuery), 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery, loadList]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  // 流式结束后刷新会话列表（新会话入库）
  useEffect(() => {
    if (!isStreaming) void loadList(searchQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming, loadList]);

  const submit = () => {
    const text = input;
    if (!text.trim() || isStreaming) return;
    setInput("");
    void send(text, expertId, webMode);
  };

  const activeExpert = experts.find((e) => e.id === expertId);

  const removeConversation = async (id: string) => {
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) reset();
    void loadList(searchQuery);
    void loadArchived(showArchived);
  };

  const patchConversation = async (
    id: string,
    patch: { title?: string; pinned?: boolean; archived?: boolean },
  ) => {
    const res = await fetch(`/api/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) return;
    void loadList(searchQuery);
    void loadArchived(showArchived);
  };

  const renameConversation = (c: ConversationItem) => {
    const next = window.prompt("重命名会话", c.title);
    if (next && next.trim() && next.trim() !== c.title) {
      void patchConversation(c.id, { title: next.trim() });
    }
  };

  /** 会话行：置顶钉标 + 悬停操作（重命名/置顶/归档/删除） */
  const conversationRow = (c: ConversationItem, isArchived: boolean) => (
    <div
      key={c.id}
      className={cn(
        "group flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors duration-150 hover:bg-surface-3",
        c.id === conversationId && "bg-brand-soft",
      )}
      onClick={() => void loadConversation(c.id)}
    >
      <div className="min-w-0">
        <div
          className={cn(
            "flex items-center gap-1 truncate text-[13px]",
            c.id === conversationId ? "text-brand" : "text-primary",
          )}
        >
          {c.pinned && <Pin size={11} className="shrink-0 text-brand" />}
          {c.title}
        </div>
        <div className="text-xs text-tertiary">{c.message_count} 条消息</div>
      </div>
      <div className="ml-2 hidden shrink-0 items-center gap-1.5 group-hover:flex">
        {!isArchived && (
          <>
            <button
              onClick={(e) => {
                e.stopPropagation();
                renameConversation(c);
              }}
              className="text-xs text-tertiary hover:text-brand"
              title="重命名"
            >
              <SquarePen size={13} />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                void patchConversation(c.id, { pinned: !c.pinned });
              }}
              className="text-xs text-tertiary hover:text-brand"
              title={c.pinned ? "取消置顶" : "置顶"}
            >
              {c.pinned ? <PinOff size={13} /> : <Pin size={13} />}
            </button>
          </>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation();
            void patchConversation(c.id, { archived: !isArchived });
          }}
          className="text-xs text-tertiary hover:text-brand"
          title={isArchived ? "取消归档" : "归档"}
        >
          <Archive size={13} />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            void removeConversation(c.id);
          }}
          className="text-xs text-tertiary hover:text-danger"
          title="删除会话"
        >
          删除
        </button>
      </div>
    </div>
  );

  const empty = messages.length === 0;

  return (
    <div className="flex h-full">
      {/* 会话列表（服务端持久化，生产切 PostgreSQL） */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-surface-2 p-3 md:flex">
        <Button className="mb-3 w-full" onClick={reset}>
          新会话
        </Button>
        {/* 会话搜索：标题 + 消息正文全文匹配 */}
        <div className="relative mb-2">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-tertiary"
          />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索会话…"
            className="h-8 w-full rounded-lg border border-line bg-surface-1 pl-7 pr-2 text-xs outline-none placeholder:text-tertiary focus:border-brand"
          />
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {conversations.length === 0 && (
            <p className="px-3 py-2 text-xs text-tertiary">
              {searchQuery.trim() ? "没有匹配的会话" : "还没有会话历史"}
            </p>
          )}
          {conversations.map((c) => conversationRow(c, false))}

          {/* 归档区折叠：默认收起，展开后单独拉取归档会话 */}
          <button
            onClick={() => {
              setShowArchived((v) => !v);
              void loadArchived(!showArchived);
            }}
            className="mt-1 flex w-full items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-tertiary transition-colors hover:bg-surface-3 hover:text-secondary"
          >
            <span className={cn("transition-transform", showArchived && "rotate-90")}>&#9656;</span>
            <Archive size={12} />
            已归档{archived.length > 0 ? `（${archived.length}）` : ""}
          </button>
          {showArchived && archived.map((c) => conversationRow(c, true))}
        </div>
      </aside>

      {/* 聊天流：与智能体页同一条内容基准线（800px 列宽，正文约 752px） */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="conversation-column">
            {empty ? (
              <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
                {activeExpert ? (
                  <>
                    <span className="mb-3 text-4xl">{activeExpert.emoji}</span>
                    <h1 className="text-xl font-medium">{activeExpert.name}</h1>
                    <p className="mt-2 max-w-[480px] text-[13px] text-secondary">
                      {activeExpert.greeting || activeExpert.description}
                    </p>
                    {(activeExpert.suggestions?.length ?? 0) > 0 && (
                      <div className="mt-5 flex max-w-[520px] flex-wrap justify-center gap-1.5">
                        {activeExpert.suggestions!.map((q) => (
                          <button
                            key={q}
                            onClick={() => setInput(q)}
                            className="rounded-full border border-line bg-surface-1 px-3 py-1.5 text-xs text-secondary transition-colors duration-150 hover:border-brand hover:text-brand"
                          >
                            {q}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <h1 className="text-xl font-medium">有什么可以帮你？</h1>
                    <p className="mt-2 text-[13px] text-secondary">
                      直接提问，或在下方选择专家获得专业视角。回答将带知识库引用与思考过程。
                    </p>
                  </>
                )}
              </div>
            ) : (
              <div className="space-y-4 py-6">
                {messages.map((m, i) => (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    conversationId={conversationId}
                    turn={Math.ceil((i + 1) / 2)}
                  />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        {/* 输入区 */}
        <div className="conversation-column shrink-0 pb-6">
          {/* 专家选择行 */}
          {experts.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <button
                onClick={() => setExpertId(null)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs transition-colors duration-150",
                  expertId === null
                    ? "border-brand bg-brand-soft text-brand"
                    : "border-line text-secondary hover:border-line-strong",
                )}
              >
                默认助手
              </button>
              {experts.map((e) => (
                <button
                  key={e.id}
                  onClick={() => setExpertId(e.id)}
                  title={e.description}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs transition-colors duration-150",
                    expertId === e.id
                      ? "border-brand bg-brand-soft text-brand"
                      : "border-line text-secondary hover:border-line-strong",
                  )}
                >
                  {e.emoji} {e.name}
                </button>
              ))}
              {activeExpert && activeExpert.kb_ids.length > 0 && (
                <span className="text-xs text-tertiary">
                  已挂载 {activeExpert.kb_ids.length} 个知识库
                </span>
              )}
            </div>
          )}
          <ComposerFrame data-composer-frame>
            <ComposerInput
              value={input}
              onChange={setInput}
              onSubmit={submit}
              autoFocus
              placeholder="输入消息，Enter 发送，Shift + Enter 换行"
              className="min-h-[52px]"
            />
            <ComposerToolbar
              left={
                <>
                  <button
                    type="button"
                    onClick={() => setWebMode(WEB_NEXT[webMode])}
                    title="联网模式：自动 → 始终 → 关闭"
                    className={cn(
                      "flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors duration-150",
                      webMode === "off"
                        ? "border-line text-tertiary hover:border-line-strong"
                        : "border-brand bg-brand-soft text-brand",
                    )}
                  >
                    <Globe size={12} />
                    联网 · {WEB_LABEL[webMode]}
                  </button>
                  <span className="hidden truncate text-xs text-tertiary sm:inline">
                    {WEB_HINT[webMode]}
                  </span>
                </>
              }
              right={
                <ComposerSendButton
                  running={isStreaming}
                  disabled={!input.trim()}
                  onSend={submit}
                  onStop={stop}
                  labels={{ send: "发送", stop: "停止回答" }}
                />
              }
            />
          </ComposerFrame>
          <p className="mt-2 text-center text-xs text-tertiary">
            内容由 AI 生成，请自行核实重要信息
          </p>
        </div>
      </section>
    </div>
  );
}
