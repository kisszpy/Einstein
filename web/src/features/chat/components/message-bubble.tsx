import { useState } from "react";
import { Markdown } from "./Markdown";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "../chat-types";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const [reasoningOpen, setReasoningOpen] = useState(false);

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
            {message.error && (
              <p className="mt-2 rounded-lg bg-danger/10 px-2 py-1 text-xs text-danger">
                {message.error}
              </p>
            )}
            {message.citations && message.citations.length > 0 && !message.streaming && (
              <div className="mt-2 border-t border-line pt-2">
                <p className="mb-1 text-xs text-tertiary">引用来源</p>
                <div className="flex flex-wrap gap-1">
                  {message.citations.map((c) => (
                    <span
                      key={c.chunk_id}
                      title={c.text}
                      className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary"
                    >
                      [{c.index}] {c.document_name} · {c.score.toFixed(2)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
