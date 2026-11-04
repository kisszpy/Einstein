import { useCallback, useRef, useState } from "react";
import { readSseStream } from "./sse-parser";
import type { ChatMessage, Citation } from "./chat-types";

let seq = 0;
const nextId = () => `msg-${Date.now()}-${seq++}`;

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  /** 从服务端历史加载一个会话 */
  const loadConversation = useCallback(async (id: string) => {
    if (isStreaming) return;
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) return;
    const data = (await res.json()) as {
      id: string;
      messages: Array<{ role: string; content: string }>;
    };
    setConversationId(data.id);
    setMessages(
      data.messages.map((m, i) => ({
        id: `hist-${data.id}-${i}`,
        role: m.role === "assistant" ? "assistant" : "user",
        content: m.content,
      })),
    );
  }, [isStreaming]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;

      const userMsg: ChatMessage = { id: nextId(), role: "user", content: trimmed };
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: "assistant",
        content: "",
        streaming: true,
      };
      const history = [...messages, userMsg];
      setMessages([...history, assistantMsg]);
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const patchAssistant = (patch: Partial<ChatMessage>) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMsg.id ? { ...m, ...patch } : m)),
        );
      };

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: conversationId,
            messages: history.map((m) => ({ role: m.role, content: m.content })),
          }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        await readSseStream(res.body, (event, raw) => {
          switch (event) {
            case "meta":
              setConversationId((raw as { conversation_id: string }).conversation_id);
              break;
            case "delta": {
              const { text } = raw as { text: string };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsg.id ? { ...m, content: m.content + text } : m,
                ),
              );
              break;
            }
            case "reasoning": {
              const { text } = raw as { text: string };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, reasoning: (m.reasoning ?? "") + text }
                    : m,
                ),
              );
              break;
            }
            case "citations":
              patchAssistant({
                citations: (raw as { citations: Citation[] }).citations,
              });
              break;
            case "error":
              patchAssistant({
                error: (raw as { message: string }).message,
                streaming: false,
              });
              break;
            case "done":
              patchAssistant({ streaming: false });
              break;
          }
        });
        patchAssistant({ streaming: false });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          patchAssistant({
            error: `请求失败：${(err as Error).message}`,
            streaming: false,
          });
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [messages, conversationId, isStreaming],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setConversationId(null);
    setIsStreaming(false);
  }, []);

  return { messages, isStreaming, conversationId, send, stop, reset, loadConversation };
}
