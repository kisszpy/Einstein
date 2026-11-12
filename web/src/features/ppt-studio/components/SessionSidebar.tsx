/**
 * 左栏会话列表：新建 + 历史 PPT 会话（标题 + 状态 + 时间）。
 * 样式语言与 agents 的 TaskSidebar 保持一致（同一天分组/悬停删除）。
 */

import { useMemo, useState } from "react";
import { Plus, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { dayBucket, relativeTime } from "@/features/agents/types";
import type { PptSession } from "../types";

const GROUP_ORDER = ["今天", "昨天", "近 7 天", "更早"];

const STATUS_LABEL: Record<string, string> = {
  idle: "",
  analyzing: "分析中",
  awaiting_confirm: "待确认",
  generating: "生成中",
  done: "已完成",
  failed: "失败",
};

interface Props {
  sessions: PptSession[];
  activeSessionId: string | null;
  onSelect: (session: PptSession) => void;
  onDelete: (session: PptSession) => void;
  onNew: () => void;
}

export function SessionSidebar({ sessions, activeSessionId, onSelect, onDelete, onNew }: Props) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = sessions.filter((s) => !q || s.title.toLowerCase().includes(q));
    const map = new Map<string, PptSession[]>();
    for (const session of matched) {
      const key = dayBucket(session.updated_at);
      map.set(key, [...(map.get(key) ?? []), session]);
    }
    return GROUP_ORDER.filter((k) => map.has(k)).map((k) => [k, map.get(k)!] as const);
  }, [sessions, query]);

  return (
    <aside aria-label="演示会话" className="flex w-[264px] shrink-0 flex-col border-r border-line bg-surface-2">
      <div className="px-3 pb-1.5 pt-3">
        <button
          onClick={onNew}
          className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-brand-soft text-[13px] font-medium text-brand-ink transition-colors duration-150 hover:bg-brand/20 "
        >
          <Plus size={14} strokeWidth={2} />
          新建演示
        </button>
      </div>

      <div className="px-3 pb-1.5 pt-1">
        <div className="relative">
          <Search
            size={13}
            strokeWidth={2}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-tertiary"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索演示会话"
            className="h-8 w-full rounded-lg bg-surface-3 pl-7 pr-2 text-[12.5px] text-primary outline-none transition-colors duration-150 placeholder:text-placeholder focus:bg-surface-1 focus:ring-1 focus:ring-line-strong"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {groups.length === 0 && (
          <p className="px-2 py-6 text-center text-[12px] text-tertiary">
            {query ? "没有匹配的会话" : "还没有演示会话，描述一个主题开始"}
          </p>
        )}
        {groups.map(([title, items]) => (
          <div key={title} className="mb-1">
            <p className="px-2 py-1.5 text-[12px] font-medium text-tertiary">{title}</p>
            <div className="space-y-px">
              {items.map((session) => {
                const active = session.id === activeSessionId;
                const status = STATUS_LABEL[session.status] ?? "";
                return (
                  <div
                    key={session.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(session)}
                    onKeyDown={(e) => e.key === "Enter" && onSelect(session)}
                    className={cn(
                      "group relative flex w-full cursor-pointer items-center gap-1 rounded-lg px-2 py-1.5 text-left transition-colors duration-150",
                      active ? "bg-brand-soft" : "hover:bg-hover",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "absolute left-0 top-1/2 h-3.5 w-[2.5px] -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-150",
                        active ? "opacity-100" : "opacity-0",
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block truncate text-[12.5px] leading-5",
                          active ? "text-brand-ink " : "text-secondary",
                        )}
                      >
                        {session.title}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1 text-[12px] text-tertiary">
                        <span className="truncate">{relativeTime(session.updated_at)}</span>
                        {status && <span>· {status}</span>}
                        {session.total_slides > 0 && <span>· {session.total_slides} 页</span>}
                      </span>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(session);
                      }}
                      title="删除会话（已生成的文件保留在项目里）"
                      className="shrink-0 rounded p-1 text-tertiary opacity-0 transition-all duration-150 hover:bg-danger/10 hover:text-danger focus:opacity-100 group-hover:opacity-100"
                    >
                      <Trash2 size={12.5} strokeWidth={1.9} />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
