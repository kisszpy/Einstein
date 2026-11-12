import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, ArrowDown, Download, Globe, Menu, Pencil, Pin, PinOff, Plus, Search, Share2, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/BrandMark";
import {
  ComposerFrame,
  ComposerInput,
  ComposerSendButton,
  ComposerToolbar,
} from "@/components/ui/composer";
import { useChatStream } from "./use-chat-stream";
import { MessageBubble } from "./components/message-bubble";
import type { WebMode } from "./chat-types";
import { ShareDialog } from "@/features/share/ShareDialog";
import { cn } from "@/lib/utils";
import { confirmDialog, promptDialog } from "@/lib/confirm";

const WEB_KEY = "einstein.chat.webMode";
const WEB_LABEL: Record<WebMode, string> = { auto: "自动", on: "始终", off: "关闭" };
const WEB_HINT: Record<WebMode, string> = {
  auto: "需要最新信息时才联网",
  on: "本轮强制联网检索",
  off: "本轮只用已有知识",
};

/** 空会话的起步建议：都是知识库场景（问答/总结/对比），点一下填进输入框 */
const CHAT_STARTERS = [
  "根据知识库里的资料，总结接入步骤",
  "帮我梳理这份文档的关键风险点",
  "对比知识库中两份文档的差异",
  "这份资料讲了什么？用三句话说清",
];

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
  const {
    messages,
    isStreaming,
    conversationId,
    send,
    regenerateAt,
    resendFrom,
    stop,
    reset,
    loadConversation,
    switchBlocked,
  } = useChatStream();
  const [input, setInput] = useState("");
  /** 正在编辑的 user 消息下标：发送时截断它及其之后的消息 */
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<ConversationItem[]>([]);
  const [experts, setExperts] = useState<ExpertItem[]>([]);
  const [expertId, setExpertId] = useState<string | null>(null);
  const [webMode, setWebMode] = useState<WebMode>(readWebMode);
  /** 正在分享的会话：null 表示面板关闭 */
  const [shareTarget, setShareTarget] = useState<{ id: string; title: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  /** 窄屏（<768px）会话列表抽屉：原先是 hidden md:flex 直接消失，分屏用户无法切会话 */
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(WEB_KEY, webMode);
  }, [webMode]);

  const loadList = useCallback(
    async (q = "", signal?: AbortSignal) => {
      const url = q.trim()
        ? `/api/conversations?q=${encodeURIComponent(q.trim())}`
        : "/api/conversations";
      try {
        const res = await fetch(url, { signal });
        if (!res.ok) return;
        setConversations(await res.json());
      } catch {
        /* 竞态取消（AbortError）与新请求失败都静默：旧结果不允许覆盖新结果 */
      }
    },
    [],
  );

  // 搜索防抖 + 竞态取消：停顿 300ms 才请求，快速输入时取消上一笔在途请求
  const searchAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    const timer = window.setTimeout(() => void loadList(searchQuery, controller.signal), 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery, loadList]);

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

  // 贴底才跟随：用户上滚回看时不再被流式 delta 强行拽回底部（>80px 视为离开底部）
  const [stickBottom, setStickBottom] = useState(true);
  const scrollHostRef = useRef<HTMLDivElement>(null);
  const onScrollHostScroll = () => {
    const el = scrollHostRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setStickBottom((prev) => (prev === near ? prev : near));
  };
  useEffect(() => {
    if (!stickBottom) return;
    bottomRef.current?.scrollIntoView({
      // 流式高频滚动用 auto 瞬时跳，避免 smooth 动画队列堆积卡顿
      behavior: isStreaming ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, isStreaming, stickBottom]);
  const scrollToLatest = () => {
    setStickBottom(true);
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  // 流式结束后刷新会话列表（新会话入库）
  useEffect(() => {
    if (!isStreaming) void loadList(searchQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming, loadList]);

  const submit = () => {
    const text = input;
    if (!text.trim() || isStreaming) return;
    setInput("");
    // 编辑态：截断该条提问及其之后的消息，重新走一次普通请求
    const editTarget = editingIndex !== null ? messages[editingIndex] : undefined;
    if (editingIndex !== null && editTarget?.role === "user") {
      const index = editingIndex;
      setEditingIndex(null);
      void resendFrom(index, text, expertId, webMode);
      return;
    }
    setEditingIndex(null);
    void send(text, expertId, webMode);
  };

  /** messagesRef 供 useCallback 回调读取最新列表，避免依赖 messages 每帧重建回调 */
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const cancelEdit = () => {
    setEditingIndex(null);
    setInput("");
  };

  const activeExpert = experts.find((e) => e.id === expertId);

  const removeConversation = async (id: string) => {
    // 误触即丢全部历史：删除前必须确认（danger 红键）
    const ok = await confirmDialog({
      title: "删除会话",
      message: "删除后聊天记录无法恢复，确定删除吗？",
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) {
      cancelEdit();
      reset();
    }
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

  const renameConversation = async (c: ConversationItem) => {
    const next = await promptDialog({ title: "重命名会话", defaultValue: c.title });
    if (next && next.trim() && next.trim() !== c.title) {
      void patchConversation(c.id, { title: next.trim() });
    }
  };

  /**
   * 导出会话。走 fetch 而不是 `<a download>`：鉴权是 Authorization 头，
   * 直接开链接带不上，会拿到 401。
   */
  const exportConversation = async (c: ConversationItem, format: "md" | "json") => {
    const res = await fetch(`/api/conversations/${c.id}/export?format=${format}`);
    if (!res.ok) return;
    const blob = await res.blob();
    // 文件名以服务端给的为准：它做了中文与非法字符处理，前端重复一遍容易两边不一致
    const disp = res.headers.get("Content-Disposition") || "";
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disp)?.[1];
    const fallback = /filename="([^"]+)"/i.exec(disp)?.[1];
    const name = utf8
      ? decodeURIComponent(utf8)
      : fallback || `${c.title}.${format}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  /** 会话行：置顶钉标 + 悬停操作（重命名/置顶/导出/归档/删除） */
  const conversationRow = (c: ConversationItem, isArchived: boolean) => (
    <div
      key={c.id}
      className={cn(
        "group flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors duration-150 hover:bg-surface-3",
        c.id === conversationId && "bg-brand-soft",
      )}
      onClick={() => {
        cancelEdit();
        setDrawerOpen(false);
        void loadConversation(c.id);
      }}
    >
      <div className="min-w-0">
        <div
          className={cn(
            "flex items-center gap-1 truncate text-[13px]",
            c.id === conversationId ? "text-brand-ink ": "text-primary",
          )}
        >
          {c.pinned && <Pin size={11} className="shrink-0 text-brand-ink "/>}
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
              className="text-xs text-tertiary hover:text-brand-ink"
              title="重命名"
            >
              <SquarePen size={13} />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                void patchConversation(c.id, { pinned: !c.pinned });
              }}
              className="text-xs text-tertiary hover:text-brand-ink"
              title={c.pinned ? "取消置顶" : "置顶"}
            >
              {c.pinned ? <PinOff size={13} /> : <Pin size={13} />}
            </button>
          </>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation();
            void exportConversation(c, e.shiftKey ? "json" : "md");
          }}
          className="text-xs text-tertiary hover:text-brand-ink"
          title="导出 Markdown（按住 Shift 导出 JSON）"
        >
          <Download size={13} />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setShareTarget({ id: c.id, title: c.title });
          }}
          className="text-xs text-tertiary hover:text-brand-ink"
          title="对外分享（生成免登录链接）"
        >
          <Share2 size={13} />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            void patchConversation(c.id, { archived: !isArchived });
          }}
          className="text-xs text-tertiary hover:text-brand-ink"
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

  /** MessageBubble 是 memo 组件：回调必须引用稳定，否则 memo 白做。
   *  index 由组件自己带上，这里不再渲染期新建箭头函数。 */
  const handleRegenerate = useCallback(
    (index: number) => void regenerateAt(index, expertId, webMode),
    [regenerateAt, expertId, webMode],
  );
  const handleEdit = useCallback((index: number) => {
    const m = messagesRef.current[index];
    if (m?.role === "user") {
      setEditingIndex(index);
      setInput(m.content);
    }
  }, []);

  // 轮次按出现的 user 消息编号：重新生成会多出助手消息，不能按序号除 2
  let turnNo = 0;
  const turnOf = messages.map((m) => {
    if (m.role === "user") turnNo += 1;
    return Math.max(turnNo, 1);
  });
  /** 某条回答对应的用户问题（差评转评测用例时用） */
  const queryOf = (index: number) => {
    for (let j = index - 1; j >= 0; j -= 1) {
      const m = messages[j];
      if (m?.role === "user") return m.content;
    }
    return "";
  };

  return (
    <div className="flex h-full">
      {/* 窄屏遮罩：抽屉打开时挡住主区，点一下收起 */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      )}
      {/* 会话列表（服务端持久化）：md 以上常驻，以下为滑入抽屉 */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r border-line bg-surface-2 p-3 transition-transform duration-200 md:static md:translate-x-0",
          drawerOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
      >
        <Button
          variant="soft"
          className="mb-3 w-full gap-1.5"
          onClick={() => {
            cancelEdit();
            reset();
          }}
        >
          <Plus size={14} strokeWidth={2} />
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

      {/* 聊天流：与智能体页同一条内容基准线。滚动区与输入区都留滚动条槽（gutter），
          消息多到出现滚动条时正文列也不会比输入列窄一截。 */}
      <section className="relative flex min-w-0 flex-1 flex-col">
        {/* 窄屏汉堡入口：md 以上隐藏 */}
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          title="会话列表"
          aria-label="打开会话列表"
          className="absolute left-3 top-3 z-20 flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-1 text-secondary shadow-float transition-colors hover:text-primary md:hidden"
        >
          <Menu size={16} />
        </button>
        <div
          ref={scrollHostRef}
          onScroll={onScrollHostScroll}
          className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]"
        >
          <div className="conversation-column">
            {empty ? (
              <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
                {activeExpert ? (
                  <>
                    <span className="mb-3 text-4xl">{activeExpert.emoji}</span>
                    <h1 className="text-[21px] font-semibold">{activeExpert.name}</h1>
                    <p className="mt-2 max-w-[480px] text-[13px] text-secondary">
                      {activeExpert.greeting || activeExpert.description}
                    </p>
                    {(activeExpert.suggestions?.length ?? 0) > 0 && (
                      <div className="mt-5 flex max-w-[520px] flex-wrap justify-center gap-1.5">
                        {activeExpert.suggestions!.map((q) => (
                          <button
                            key={q}
                            onClick={() => setInput(q)}
                            className="rounded-full border border-line bg-surface-1 px-3 py-1.5 text-xs text-secondary transition-colors duration-150 hover:border-brand hover:text-brand-ink"
                          >
                            {q}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <BrandMark size={44} className="mb-4 rounded-[13px] shadow-[0_14px_30px_-16px_var(--brand)]" />
                    <h1 className="text-[21px] font-semibold">有什么可以帮你？</h1>
                    <p className="mt-2 text-[13px] text-secondary">
                      直接提问，或在下方选择专家获得专业视角。回答将带知识库引用与思考过程。
                    </p>
                    {/* 与专家视角的建议条同一套交互：点一下填进输入框，降低"第一句说什么"的门槛 */}
                    <div className="mt-5 flex max-w-[560px] flex-wrap justify-center gap-1.5">
                      {CHAT_STARTERS.map((q) => (
                        <button
                          key={q}
                          onClick={() => setInput(q)}
                          className="rounded-full border border-line bg-surface-1 px-3 py-1.5 text-xs text-secondary transition-colors duration-150 hover:border-brand hover:text-brand-ink"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="space-y-4 py-6">
                {messages.map((m, i) => (
                  <MessageBubble
                    key={m.id}
                    index={i}
                    message={m}
                    conversationId={conversationId}
                    turn={turnOf[i]}
                    userQuery={m.role === "assistant" ? queryOf(i) : undefined}
                    busy={isStreaming}
                    onRegenerate={m.role === "assistant" ? handleRegenerate : undefined}
                    onEdit={m.role === "user" ? handleEdit : undefined}
                  />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        {/* 回到最新：用户上滚回看且流式仍在继续时出现，避免被贴底逻辑拽走后找不到入口 */}
        {!stickBottom && !empty && (
          <button
            type="button"
            onClick={scrollToLatest}
            className="absolute bottom-28 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-surface-1 px-3 py-1.5 text-xs text-secondary shadow-float transition-colors duration-150 hover:border-line-strong hover:text-primary"
          >
            <ArrowDown size={12} />
            回到最新
          </button>
        )}

        {/* 输入区：外层用同样的滚动条槽占位，保证与消息区逐像素同宽 */}
        <div className="shrink-0 overflow-y-hidden [scrollbar-gutter:stable]">
          <div className="conversation-column pb-6">
          {/* 流式中点历史会话被挡下时的显式提示：让"点了没反应"变成"知道为什么" */}
          {isStreaming && switchBlocked && (
            <div className="mb-2 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-primary">
              <span className="min-w-0 flex-1">正在生成回答，请先停止再切换会话</span>
              <button
                type="button"
                onClick={stop}
                className="shrink-0 rounded px-1.5 py-0.5 font-medium transition-colors hover:text-danger"
              >
                停止生成
              </button>
            </div>
          )}
          {/* 专家选择行 */}
          {experts.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <button
                onClick={() => setExpertId(null)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs transition-colors duration-150",
                  expertId === null
                    ? "border-brand bg-brand-soft text-brand-ink "
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
                      ? "border-brand bg-brand-soft text-brand-ink "
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
          {/* 编辑态提示：说明这次发送会截断旧内容 */}
          {editingIndex !== null && (
            <div className="mb-2 flex items-center gap-2 rounded-lg border border-brand/40 bg-brand-soft px-3 py-1.5 text-xs text-brand-ink ">
              <Pencil size={12} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate">
                正在编辑第 {turnOf[editingIndex] ?? 1} 轮提问，发送后这条提问之后的内容将被替换
              </span>
              <button
                type="button"
                onClick={cancelEdit}
                className="shrink-0 text-tertiary transition-colors hover:text-secondary"
              >
                取消
              </button>
            </div>
          )}
          <ComposerFrame data-composer-frame>
            <ComposerInput
              // 进入/退出编辑态时重挂输入框，自动聚焦并重新算高度
              key={editingIndex ?? "compose"}
              value={input}
              onChange={setInput}
              onSubmit={submit}
              autoFocus
              placeholder={
                editingIndex !== null
                  ? "修改后发送，将替换这条提问之后的内容"
                  : "输入消息，Enter 发送，Shift + Enter 换行"
              }
              className="min-h-[52px]"
            />
            <ComposerToolbar
              left={
                <>
                  {/* 三态分段控件：状态全部可见可点，替代"单按钮循环切换"的不可预期 */}
                  <div
                    role="radiogroup"
                    aria-label="联网模式"
                    className="flex shrink-0 items-center gap-0.5 rounded-full border border-line p-0.5"
                  >
                    <Globe size={12} className="ml-1.5 mr-0.5 shrink-0 text-tertiary" />
                    {(Object.keys(WEB_LABEL) as WebMode[]).map((mode) => (
                      <button
                        key={mode}
                        type="button"
                        role="radio"
                        aria-checked={webMode === mode}
                        title={WEB_HINT[mode]}
                        onClick={() => setWebMode(mode)}
                        className={cn(
                          "rounded-full px-2 py-1 text-xs transition-colors duration-150",
                          webMode === mode
                            ? "bg-brand-soft font-medium text-brand-ink "
                            : "text-tertiary hover:text-secondary",
                        )}
                      >
                        {WEB_LABEL[mode]}
                      </button>
                    ))}
                  </div>
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
        </div>
      </section>

      <ShareDialog
        open={shareTarget !== null}
        onClose={() => setShareTarget(null)}
        kind="conversation"
        targetId={shareTarget?.id ?? ""}
        title={shareTarget?.title ?? ""}
      />
    </div>
  );
}
