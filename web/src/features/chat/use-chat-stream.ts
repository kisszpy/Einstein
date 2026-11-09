import { useCallback, useEffect, useRef, useState } from "react";
import { readSseStream } from "./sse-parser";
import type { ChatMessage, Citation, WebMode, WebState } from "./chat-types";

let seq = 0;
const nextId = () => `msg-${Date.now()}-${seq++}`;

interface RunOptions {
  /** 界面上保留的消息（新答案会追加在其后） */
  base: ChatMessage[];
  /** 发给服务端的消息，必须以 user 结尾 */
  payload: ChatMessage[];
  /** 重新生成：服务端不重复落库 user，且不让模型看见上一版答案 */
  regenerate: boolean;
  expertId?: string | null;
  webMode: WebMode;
}

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  /** 流式期间点历史会话会被挡下，置 true 让页面给出提示而不是静默吞掉 */
  const [switchBlocked, setSwitchBlocked] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // ---- delta 合帧：SSE 常单包多 delta，逐条 setState 每帧触发全列表重渲染。
  // 累积到 ref，用 rAF 对齐浏览器帧率批量落盘；流结束/中断时强制冲掉残余。 ----
  const pendingRef = useRef<{ id: string; content: string; reasoning: string } | null>(null);
  const rafRef = useRef(0);

  const flushPending = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const p = pendingRef.current;
    if (!p) return;
    pendingRef.current = null;
    setMessages((prev) =>
      prev.map((m) =>
        m.id === p.id
          ? {
              ...m,
              content: m.content + p.content,
              reasoning: p.reasoning ? (m.reasoning ?? "") + p.reasoning : m.reasoning,
            }
          : m,
      ),
    );
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      flushPending();
    });
  }, [flushPending]);

  // 卸载时不再触发挂起的 flush，避免向已卸载组件 setState
  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  /** 从服务端历史加载一个会话 */
  const loadConversation = useCallback(async (id: string) => {
    if (isStreaming) {
      // 以前这里静默 return，用户点了历史会话毫无反应，误以为页面卡死
      setSwitchBlocked(true);
      return;
    }
    setSwitchBlocked(false);
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) {
      // 以前这里静默 return，用户点了历史会话毫无反应，误以为页面卡死
      window.alert(
        res.status === 404
          ? "该会话不存在或已被删除。"
          : `打开会话失败（HTTP ${res.status}），请稍后重试。`,
      );
      return;
    }
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

  /** 一次请求：先插入占位气泡，再按 SSE 逐段补齐内容 */
  const run = useCallback(
    async ({ base, payload, regenerate, expertId, webMode }: RunOptions) => {
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: "assistant",
        content: "",
        streaming: true,
      };
      setMessages([...base, assistantMsg]);
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
            expert_id: expertId ?? undefined,
            web_mode: webMode,
            regenerate,
            messages: payload.map((m) => ({ role: m.role, content: m.content })),
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
              const p =
                pendingRef.current ??
                (pendingRef.current = { id: assistantMsg.id, content: "", reasoning: "" });
              p.content += text;
              scheduleFlush();
              break;
            }
            case "reasoning": {
              const { text } = raw as { text: string };
              const p =
                pendingRef.current ??
                (pendingRef.current = { id: assistantMsg.id, content: "", reasoning: "" });
              p.reasoning += text;
              scheduleFlush();
              break;
            }
            case "web":
              // 同一轮会依次收到 checking / searching / done，直接覆盖
              patchAssistant({ web: raw as WebState });
              break;
            case "citations":
              patchAssistant({
                citations: (raw as { citations: Citation[] }).citations,
              });
              break;
            case "error":
              flushPending();
              patchAssistant({
                error: (raw as { message: string }).message,
                streaming: false,
              });
              break;
            case "done":
              flushPending();
              patchAssistant({ streaming: false });
              break;
          }
        });
        flushPending();
        patchAssistant({ streaming: false });
      } catch (err) {
        flushPending(); // 中断/报错也把已收到的文本落盘，气泡里能看到已生成部分
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
    [conversationId, flushPending, scheduleFlush],
  );

  /** 普通发送：追加一条 user 后请求 */
  const send = useCallback(
    async (text: string, expertId?: string | null, webMode: WebMode = "auto") => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;
      const userMsg: ChatMessage = { id: nextId(), role: "user", content: trimmed };
      const history = [...messages, userMsg];
      await run({ base: history, payload: history, regenerate: false, expertId, webMode });
    },
    [messages, isStreaming, run],
  );

  /**
   * 重新生成第 assistantIndex 条回答。
   * - 请求体只带「该问题及之前」的消息，末尾是 user（服务端还会再截一次）
   * - 旧答案留在原位并标为「上一版」，新答案追加成新气泡，两版可对比
   * - 重新生成中途的回答会让它之后的轮次失效，因此界面只显示到这一轮
   */
  const regenerateAt = useCallback(
    async (assistantIndex: number, expertId?: string | null, webMode: WebMode = "auto") => {
      if (isStreaming) return;
      let userIdx = assistantIndex - 1;
      while (userIdx >= 0 && messages[userIdx]?.role !== "user") userIdx -= 1;
      if (userIdx < 0) return;
      const payload = messages.slice(0, userIdx + 1);
      const base = messages
        .slice(0, assistantIndex + 1)
        .map((m, i) => (i === assistantIndex ? { ...m, superseded: true } : m));
      await run({ base, payload, regenerate: true, expertId, webMode });
    },
    [messages, isStreaming, run],
  );

  /**
   * 编辑后重发：丢弃 userIndex 及其之后的消息，用新内容发一次普通请求。
   * 旧分支仍留在服务端会话历史里，只是不再显示。
   */
  const resendFrom = useCallback(
    async (
      userIndex: number,
      text: string,
      expertId?: string | null,
      webMode: WebMode = "auto",
    ) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;
      const userMsg: ChatMessage = { id: nextId(), role: "user", content: trimmed };
      const payload = [...messages.slice(0, userIndex), userMsg];
      await run({ base: payload, payload, regenerate: false, expertId, webMode });
    },
    [messages, isStreaming, run],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setConversationId(null);
    setIsStreaming(false);
  }, []);

  return {
    messages,
    isStreaming,
    conversationId,
    send,
    regenerateAt,
    resendFrom,
    stop,
    reset,
    loadConversation,
    /** 流式中点历史会话被挡下：页面据此显示提示条 */
    switchBlocked,
  };
}
