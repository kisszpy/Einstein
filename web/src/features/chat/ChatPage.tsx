import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useChatStream } from "./use-chat-stream";
import { MessageBubble } from "./components/message-bubble";
import { cn } from "@/lib/utils";

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
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadList = useCallback(async () => {
    const res = await fetch("/api/conversations");
    setConversations(await res.json());
  }, []);

  useEffect(() => {
    void loadList();
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
    void send(text);
  };

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

      {/* 聊天流（约 760px 居中） */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[760px] px-6">
            {empty ? (
              <div className="flex h-full min-h-[60vh] flex-col items-center justify-center text-center">
                <h1 className="text-xl font-medium">有什么可以帮你？</h1>
                <p className="mt-2 text-[13px] text-secondary">
                  直接提问即可。专家选择与知识库引用将在后续里程碑接入。
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
        <div className="mx-auto w-full max-w-[760px] shrink-0 px-6 pb-6">
          <Card className="flex items-end gap-2 p-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
              rows={2}
              className="max-h-40 flex-1 resize-none bg-transparent px-2 text-sm outline-none placeholder:text-tertiary"
            />
            {isStreaming ? (
              <Button variant="outline" onClick={stop}>
                停止
              </Button>
            ) : (
              <Button disabled={!input.trim()} onClick={submit}>
                发送
              </Button>
            )}
          </Card>
          <p className="mt-2 text-center text-xs text-tertiary">
            内容由 AI 生成，请自行核实重要信息
          </p>
        </div>
      </section>
    </div>
  );
}
