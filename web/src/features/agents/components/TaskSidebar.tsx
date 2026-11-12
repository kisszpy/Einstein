/**
 * 左侧任务栏：新建任务 / 搜索 / 按时间分组的历史任务。
 *
 * 这里**不再放**「项目与文件」：项目绑定在输入框的能力弹层里（Composer），
 * 文件浏览与预览在「资产」页，同页顶栏也有「预览」按钮。同一件事摆三套入口，
 * 既挤掉任务列表的可滚动高度，也让用户分不清「绑定项目」和「正在浏览哪个项目」。
 */

import { useMemo } from "react";
import { Plus, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { dayBucket, relativeTime, type TaskSession } from "../types";

interface Props {
  sessions: TaskSession[];
  activeSessionId: string | null;
  onSelectSession: (session: TaskSession) => void;
  onDeleteSession: (session: TaskSession) => void;
  onNewTask: () => void;
  query: string;
  onQuery: (value: string) => void;
}

const GROUP_ORDER = ["今天", "昨天", "近 7 天", "更早"];

export function TaskSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  onNewTask,
  query,
  onQuery,
}: Props) {
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = sessions.filter(
      (s) => !q || s.title.toLowerCase().includes(q) || s.runs.some((r) => r.task.toLowerCase().includes(q)),
    );
    const map = new Map<string, TaskSession[]>();
    for (const session of matched) {
      const key = dayBucket(session.created_at);
      map.set(key, [...(map.get(key) ?? []), session]);
    }
    return GROUP_ORDER.filter((k) => map.has(k)).map((k) => [k, map.get(k)!] as const);
  }, [sessions, query]);

  return (
    <aside className="flex w-[264px] shrink-0 flex-col border-r border-line bg-surface-2">
      <div className="px-3 pb-1.5 pt-3">
        <button
          onClick={onNewTask}
          className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-brand-soft text-[13px] font-medium text-brand-ink transition-colors duration-150 hover:bg-brand/20 "
        >
          <Plus size={14} strokeWidth={2} />
          新建任务
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
            onChange={(e) => onQuery(e.target.value)}
            placeholder="搜索任务"
            className="h-8 w-full rounded-lg bg-surface-3 pl-7 pr-2 text-[12.5px] text-primary outline-none transition-colors duration-150 placeholder:text-placeholder focus:bg-surface-1 focus:ring-1 focus:ring-line-strong"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {groups.length === 0 && (
          <p className="px-2 py-6 text-center text-[12px] text-tertiary">
            {query ? "没有匹配的任务" : "还没有任务，从一个问题开始"}
          </p>
        )}
        {groups.map(([title, items]) => (
          <div key={title} className="mb-1">
            <p className="px-2 py-1.5 text-[12px] font-medium text-tertiary">{title}</p>
            <div className="space-y-px">
              {items.map((session) => {
                const calls = session.steps;
                const active = session.id === activeSessionId;
                const turns = session.runs.length;
                return (
                  <div
                    key={session.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelectSession(session)}
                    onKeyDown={(e) => e.key === "Enter" && onSelectSession(session)}
                    className={cn(
                      "group relative flex w-full cursor-pointer items-center gap-1 rounded-lg px-2 py-1.5 text-left transition-colors duration-150",
                      active ? "bg-brand-soft" : "hover:bg-hover",
                    )}
                  >
                    {/* 选中指示条：与主侧栏导航同一套语言 */}
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
                        {turns > 1 && <span>· {turns} 轮</span>}
                        {calls > 0 && <span>· {calls} 步</span>}
                        {session.artifacts > 0 && <span>· {session.artifacts} 产物</span>}
                      </span>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSession(session);
                      }}
                      title={turns > 1 ? `删除任务（含 ${turns} 轮对话）` : "删除任务"}
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
