import { useCallback, useEffect, useRef, useState } from "react";
import { Globe } from "lucide-react";
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
}

interface ConversationItem {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export function ChatPage() {
  const { messages, isStreaming, conversationId, send, stop, reset, loadConversation } =
    useChatStream();
  const [input, setInput] = useState("");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [experts, setExperts] = useState<ExpertItem[]>([]);
  const [expertId, setExpertId] = useState<string | null>(null);
  const [webMode, setWebMode] = useState<WebMode>(readWebMode);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(WEB_KEY, webMode);
  }, [webMode]);

  const loadList = useCallback(async () => {
    const res = await fetch("/api/conversations");
    setConversations(await res.json());
  }, []);

  useEffect(() => {
    void loadList();
    void fetch("/api/experts")
      .then((r) => r.json())
      .then(setExperts);
  }, [loadList]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  // 流式结束后刷新会话列表（新会话入库）
  useEffect(() => {
    if (!isStreaming) void loadList();
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
    void loadList();
  };

  const empty = messages.length === 0;

  return (
    <div className="flex h-full">
      {/* 会话列表（服务端持久化，生产切 PostgreSQL） */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-surface-2 p-3 md:flex">
        <Button className="mb-3 w-full" onClick={reset}>
          新会话
        </Button>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {conversations.length === 0 && (
            <p className="px-3 py-2 text-xs text-tertiary">还没有会话历史</p>
          )}
          {conversations.map((c) => (
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
                    "truncate text-[13px]",
                    c.id === conversationId ? "text-brand" : "text-primary",
                  )}
                >
                  {c.title}
                </div>
                <div className="text-xs text-tertiary">{c.message_count} 条消息</div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  void removeConversation(c.id);
                }}
                className="ml-2 hidden text-xs text-tertiary hover:text-danger group-hover:block"
                title="删除会话"
              >
                删除
              </button>
            </div>
          ))}
        </div>
      </aside>

      {/* 聊天流：与智能体页同一条内容基准线（800px 列宽，正文约 752px） */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="conversation-column">
            {empty ? (
              <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
                <h1 className="text-xl font-medium">有什么可以帮你？</h1>
                <p className="mt-2 text-[13px] text-secondary">
                  直接提问，或在下方选择专家获得专业视角。回答将带知识库引用与思考过程。
                </p>
              </div>
            ) : (
              <div className="space-y-4 py-6">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
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
