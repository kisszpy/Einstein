/**
 * 左侧任务栏：新建任务 / 搜索 / 按时间分组的历史任务 / 当前项目与文件。
 * 结构与 WorkBuddy 的任务侧栏一致：顶部主操作，中部可滚动列表，底部工作区。
 */

import { useMemo, useState } from "react";
import {
  Download,
  Eye,
  FolderPlus,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  dayBucket,
  fileName,
  relativeTime,
  type ProjectInfo,
  type TaskSession,
  type TreeNode,
} from "../types";

interface Props {
  sessions: TaskSession[];
  activeSessionId: string | null;
  onSelectSession: (session: TaskSession) => void;
  onDeleteSession: (session: TaskSession) => void;
  onNewTask: () => void;
  query: string;
  onQuery: (value: string) => void;
  /** 运行绑定的项目（下拉的值） */
  project: string;
  /** 文件树/预览按钮指向的项目（浏览用，可与绑定不同） */
  browseProject: string;
  projects: ProjectInfo[];
  onSelectProject: (id: string) => void;
  onCreateProject: () => void;
  onDeleteProject: () => void;
  onRefreshTree: () => void;
  onPreviewProject: () => void;
  tree: TreeNode[];
  onOpenFile: (path: string) => void;
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
  project,
  browseProject,
  projects,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onRefreshTree,
  onPreviewProject,
  tree,
  onOpenFile,
}: Props) {
  const [treeOpen, setTreeOpen] = useState(true);

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
          className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-brand-soft text-[13px] font-medium text-brand-deep transition-colors duration-150 hover:bg-brand/20 dark:text-brand"
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
                          active ? "text-brand-deep dark:text-brand" : "text-secondary",
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

      {/* 工作区：项目与文件 */}
      <div className="shrink-0 border-t border-line px-2.5 pb-2.5 pt-2">
        <div className="flex items-center justify-between px-1">
          <span className="text-[12px] font-medium text-tertiary">工作区</span>
          <div className="flex items-center gap-0.5">
            <button
              onClick={onPreviewProject}
              disabled={!browseProject}
              title="在右侧内置浏览器里打开项目"
              className="rounded p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              <Eye size={12.5} strokeWidth={1.9} />
            </button>
            <button
              onClick={onRefreshTree}
              disabled={!browseProject}
              title="刷新文件"
              className="rounded p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              <RefreshCw size={12.5} strokeWidth={1.9} />
            </button>
            <button
              onClick={onCreateProject}
              title="新建项目"
              className="rounded p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary"
            >
              <FolderPlus size={12.5} strokeWidth={1.9} />
            </button>
            {browseProject && (
              <>
                <a
                  href={`/api/projects/${encodeURIComponent(browseProject)}/archive`}
                  title="下载整个项目（zip）"
                  className="rounded p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary"
                >
                  <Download size={12.5} strokeWidth={1.9} />
                </a>
                <button
                  onClick={onDeleteProject}
                  title="删除项目"
                  className="rounded p-1.5 text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger"
                >
                  <Trash2 size={12.5} strokeWidth={1.9} />
                </button>
              </>
            )}
          </div>
        </div>

        <select
          value={project}
          onChange={(e) => onSelectProject(e.target.value)}
          aria-label="当前项目"
          className="mt-1 h-8 w-full cursor-pointer rounded-lg border border-line bg-surface-1 px-2 text-[12px] text-primary outline-none transition-colors duration-150 hover:border-line-strong focus:border-line-strong"
        >
          <option value="">运行时由智能体创建</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}（{p.files}）
            </option>
          ))}
        </select>

        {browseProject && (
          <div className="mt-1">
            <button
              onClick={() => setTreeOpen((v) => !v)}
              className="flex w-full items-center gap-1 px-1 py-1 text-[12px] text-tertiary transition-colors hover:text-secondary"
            >
              <span>{tree.length} 个文件</span>
              <span className="ml-auto">{treeOpen ? "收起" : "展开"}</span>
            </button>
            {treeOpen && (
              <div className="max-h-44 space-y-px overflow-y-auto pb-0.5">
                {tree.length === 0 && (
                  <p className="px-1.5 py-1.5 text-[12px] text-tertiary">（空项目）</p>
                )}
                {tree.map((node) => {
                  const depth = node.path.split("/").length - 1;
                  const base = { paddingLeft: `${6 + depth * 12}px` };
                  if (node.type === "dir") {
                    return (
                      <div
                        key={node.path}
                        style={base}
                        className="truncate py-1 text-[12px] text-tertiary"
                      >
                        {node.path.split("/").pop()}
                      </div>
                    );
                  }
                  return (
                    <div
                      key={node.path}
                      style={base}
                      className="group flex items-center rounded pr-1 transition-colors duration-150 hover:bg-hover"
                    >
                      <button
                        onClick={() => onOpenFile(node.path)}
                        title="在右侧预览（不改页面）"
                        className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left text-[12px] text-secondary transition-colors hover:text-primary"
                      >
                        <span className="truncate">{fileName(node.path)}</span>
                      </button>
                      <a
                        href={`/api/projects/${encodeURIComponent(browseProject)}/download?path=${encodeURIComponent(node.path)}`}
                        title="下载"
                        className="shrink-0 rounded p-0.5 text-tertiary opacity-0 transition-opacity hover:text-brand group-hover:opacity-100"
                      >
                        <Download size={11} strokeWidth={1.9} />
                      </a>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
