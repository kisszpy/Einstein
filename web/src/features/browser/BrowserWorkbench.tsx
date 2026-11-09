/**
 * 内置浏览器的工作台本体：产物 / 网页 / 演示三源。
 *
 * 抽成受控组件是为了两个容器共用一份实现——
 * 独立页 /browser（状态放 URL）和智能体页右侧的预览面板（状态放组件内）。
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  BookOpen,
  FileCode2,
  FolderOpen,
  Globe2,
  Loader2,
  PanelLeft,
  Presentation,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ArtifactView } from "./components/ArtifactView";
import { DeckEditor } from "./components/DeckEditor";
import { FileTree } from "./components/FileTree";
import { WebView } from "./WebView";
import * as api from "./api";
import { type BrowserState, type ProjectInfo, type TreeEntry } from "./types";

const TABS = [
  { id: "artifact" as const, label: "产物", icon: FolderOpen },
  { id: "web" as const, label: "网页", icon: Globe2 },
  { id: "deck" as const, label: "演示", icon: Presentation },
];

interface Props {
  value: BrowserState;
  onChange: (patch: Partial<BrowserState>) => void;
  /** 面板模式：窄、可收起文件栏、编辑器换成紧凑版式 */
  compact?: boolean;
  /** 面板头部右侧（关闭 / 展开） */
  headerRight?: ReactNode;
  /** 外部内容变化（比如智能体刚写完文件）时递增，触发文件树与预览刷新 */
  refreshToken?: number;
  className?: string;
}

export function BrowserWorkbench({
  value,
  onChange,
  compact = false,
  headerRight,
  refreshToken = 0,
  className,
}: Props) {
  const { tab, project, path, url } = value;
  const [ticket, setTicket] = useState("");
  const [ticketError, setTicketError] = useState("");
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);
  const [railOpen, setRailOpen] = useState(!compact);
  const [treeTick, setTreeTick] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .getTicket()
      .then((next) => alive && setTicket(next))
      .catch((exc: Error) => alive && setTicketError(exc.message));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    api
      .listProjects()
      .then((list) => {
        if (!alive) return;
        setProjects(list);
        // 只有一个项目时直接进去，省一次点击
        if (!project && list.length === 1 && list[0]) onChange({ project: list[0].id });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  useEffect(() => {
    if (!project) {
      setEntries([]);
      return;
    }
    let alive = true;
    setLoadingTree(true);
    api
      .projectTree(project)
      .then((data) => alive && setEntries(data.entries ?? []))
      .catch(() => alive && setEntries([]))
      .finally(() => alive && setLoadingTree(false));
    return () => {
      alive = false;
    };
  }, [project, refreshToken, treeTick]);

  const files = useMemo(() => entries.filter((entry) => entry.type === "file"), [entries]);
  const decks = useMemo(
    () => files.filter((file) => file.path.toLowerCase().endsWith(".pptx")),
    [files],
  );
  const activeProject = projects.find((item) => item.id === project);
  const railWidth = compact ? 176 : 212;
  const showRail = tab !== "web" && railOpen;

  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col", className)}>
      {/* 顶栏：三个来源 + 当前上下文 */}
      <div className="flex shrink-0 items-center gap-1 border-b border-line px-2.5">
        <div className="flex items-center gap-0.5 py-1.5">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => {
                // 切到演示时，当前选中的普通文件不属于这个标签，清掉等用户挑 pptx
                const keepPath = id === "deck" ? path.toLowerCase().endsWith(".pptx") : true;
                onChange(keepPath ? { tab: id } : { tab: id, path: "" });
                // 面板模式默认收起文件栏；切到产物/演示时摊开，免得只看到空状态
                if (compact && id !== "web") setRailOpen(true);
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px] transition-colors",
                tab === id
                  ? "bg-brand-soft font-medium text-brand-deep dark:text-brand"
                  : "text-secondary hover:bg-surface-3 hover:text-primary",
              )}
              title={label}
              aria-label={label}
              aria-pressed={tab === id}
            >
              <Icon size={13} strokeWidth={1.8} />
              {compact ? (tab === id ? label : "") : label}
            </button>
          ))}
        </div>

        <span className="mx-0.5 h-4 w-px bg-line-strong" />

        {compact && tab !== "web" && (
          <button
            onClick={() => setRailOpen((prev) => !prev)}
            title={railOpen ? "收起文件栏" : "展开文件栏"}
            className={cn(
              "rounded-md p-1.5 transition-colors",
              railOpen ? "text-brand-deep dark:text-brand": "text-tertiary hover:bg-surface-3 hover:text-secondary",
            )}
          >
            <PanelLeft size={13} strokeWidth={1.8} />
          </button>
        )}

        <select
          value={project}
          aria-label="切换项目"
          onChange={(event) => onChange({ project: event.target.value, path: "" })}
          className="max-w-[168px] rounded-md border border-line bg-surface-2 px-2 py-1 text-[12px] text-secondary outline-none"
        >
          <option value="">选择项目…</option>
          {projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}（{item.files}）
            </option>
          ))}
        </select>

        {!compact && (
          <span className="ml-auto flex items-center gap-2 pr-1 text-[12px] text-tertiary">
            {activeProject && tab !== "web" && (
              <>
                <span>{activeProject.files} 个文件</span>
                <span className="h-3 w-px bg-line" />
                <span>{activeProject.size}</span>
                <span className="h-3 w-px bg-line" />
              </>
            )}
            <span>票据 {ticket ? "已签发" : ticketError ? "获取失败" : "…"}</span>
          </span>
        )}

        <div className={cn("flex items-center gap-1", compact ? "ml-auto pr-0.5" : "")}>
          {headerRight}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {showRail && (
          <aside
            className="flex shrink-0 flex-col border-r border-line bg-surface-2"
            style={{ width: railWidth }}
          >
            <div className="flex items-center justify-between px-2.5 pb-1 pt-2 text-[12px] tracking-wide text-tertiary">
              <span className="flex items-center gap-1">
                {tab === "deck" ? "演示文稿" : "项目文件"}
                {loadingTree && <Loader2 size={10} className="animate-spin" />}
              </span>
              <button
                onClick={() => setTreeTick((n) => n + 1)}
                title="刷新文件列表"
                className="rounded p-2 -m-1.5 text-tertiary hover:bg-surface-3 hover:text-primary"
              >
                <RefreshCw size={10} strokeWidth={2} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {project ? (
                files.length > 0 ? (
                  tab === "deck" ? (
                    <DeckList decks={decks} active={path} onPick={(next) => onChange({ tab: "deck", path: next })} />
                  ) : (
                    <FileTree
                      entries={files}
                      active={path}
                      onPick={(next) =>
                        onChange(
                          next.toLowerCase().endsWith(".pptx")
                            ? { tab: "deck", path: next }
                            : { tab: "artifact", path: next },
                        )
                      }
                    />
                  )
                ) : (
                  <p className="px-2.5 py-6 text-[12px] leading-relaxed text-tertiary">
                    {tab === "deck" ? "这个项目还没有 pptx" : "这个项目还没有文件"}
                  </p>
                )
              ) : (
                <p className="px-2.5 py-6 text-[12px] leading-relaxed text-tertiary">
                  先在上面选一个项目
                </p>
              )}
            </div>
          </aside>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {tab === "web" && (
            <WebView url={url} ticket={ticket} onNavigate={(next) => onChange({ url: next })} />
          )}

          {tab === "artifact" &&
            (project ? (
              path ? (
                <ArtifactView
                  project={project}
                  path={path}
                  ticket={ticket}
                  refreshToken={refreshToken}
                  onEditDeck={() => onChange({ tab: "deck", path })}
                />
              ) : (
                <ProjectOverview
                  project={project}
                  projectInfo={activeProject}
                  files={files}
                  onPick={(next) => onChange({ path: next })}
                />
              )
            ) : (
              <ProjectGrid projects={projects} onPick={(id) => onChange({ project: id, path: "" })} />
            ))}

          {tab === "deck" &&
            (project && path ? (
              <DeckEditor
                project={project}
                path={path}
                compact={compact}
                onClose={() => onChange({ path: "" })}
              />
            ) : (
              <EmptyDeck hasProject={Boolean(project)} hasDecks={decks.length > 0} />
            ))}
        </div>
      </div>
    </div>
  );
}

function DeckList({
  decks,
  active,
  onPick,
}: {
  decks: TreeEntry[];
  active: string;
  onPick: (path: string) => void;
}) {
  return (
    <div className="py-1">
      {decks.map((item) => (
        <button
          key={item.path}
          onClick={() => onPick(item.path)}
          className={cn(
            "flex w-full items-center gap-2 px-2.5 py-2 text-left text-[12.5px]",
            active === item.path
              ? "bg-brand-soft text-brand-deep dark:text-brand"
              : "text-secondary hover:bg-surface-3 hover:text-primary",
          )}
          title={item.path}
        >
          <Presentation size={13} className="shrink-0 opacity-70" />
          <span className="truncate">{item.path.split("/").pop()}</span>
        </button>
      ))}
    </div>
  );
}

function ProjectGrid({
  projects,
  onPick,
}: {
  projects: ProjectInfo[];
  onPick: (id: string) => void;
}) {
  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <h2 className="text-[14px] font-medium">选择一个项目</h2>
      <p className="mt-1 text-[12px] text-tertiary">
        智能体创建的项目都在这里，点进去就能预览产物、改文件。
      </p>
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {projects.map((item) => (
          <button
            key={item.id}
            onClick={() => onPick(item.id)}
            className="group flex flex-col items-start gap-2 rounded-xl border border-line bg-surface-1 p-4 text-left transition-colors hover:border-brand/40 hover:bg-brand-soft/40"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-3 text-secondary group-hover:bg-brand-soft group-hover:text-brand">
              <FolderOpen size={15} strokeWidth={1.8} />
            </span>
            <span className="w-full truncate text-[13px] font-medium">{item.name}</span>
            <span className="text-[12px] text-tertiary">
              {item.files} 个文件 · {item.size}
            </span>
            <span className="text-[12px] text-tertiary">更新于 {item.updated_at}</span>
          </button>
        ))}
      </div>
      {projects.length === 0 && (
        <p className="mt-10 text-center text-[12.5px] text-tertiary">
          还没有项目。去「智能体」页让它建一个试试。
        </p>
      )}
    </div>
  );
}

function ProjectOverview({
  project,
  projectInfo,
  files,
  onPick,
}: {
  project: string;
  projectInfo?: ProjectInfo;
  files: TreeEntry[];
  onPick: (path: string) => void;
}) {
  const grouped = files.reduce<Record<string, TreeEntry[]>>((acc, file) => {
    const dir = file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : "根目录";
    acc[dir] = [...(acc[dir] ?? []), file];
    return acc;
  }, {});

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <h2 className="text-[15px] font-medium">{projectInfo?.name || project}</h2>
      <p className="mt-1 text-[12px] text-tertiary">
        {files.length} 个文件 · {projectInfo?.size || "—"} · 从左侧或下面的列表打开
      </p>
      <div className="mt-5 space-y-4">
        {Object.entries(grouped).map(([dir, list]) => (
          <div key={dir}>
            <p className="mb-1.5 flex items-center gap-1.5 text-[12px] tracking-wide text-tertiary">
              <FolderOpen size={11} /> {dir}
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {list.map((file) => (
                <button
                  key={file.path}
                  onClick={() => onPick(file.path)}
                  className="flex items-center gap-2 rounded-lg border border-line bg-surface-1 px-3 py-2 text-left hover:border-brand/40 hover:bg-brand-soft/30"
                >
                  {file.path.endsWith(".html") ? (
                    <FileCode2 size={13} className="shrink-0 text-brand-deep dark:text-brand"/>
                  ) : file.path.endsWith(".pptx") ? (
                    <Presentation size={13} className="shrink-0 text-warning" />
                  ) : (
                    <BookOpen size={13} className="shrink-0 text-tertiary" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">
                    {file.path.split("/").pop()}
                  </span>
                  <span className="shrink-0 text-[12px] text-tertiary">{file.size}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {files.length === 0 && (
        <p className="mt-8 text-[12.5px] text-tertiary">这个项目还是空的。</p>
      )}
    </div>
  );
}

function EmptyDeck({ hasProject, hasDecks }: { hasProject: boolean; hasDecks: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand-deep dark:text-brand">
        <Presentation size={20} strokeWidth={1.6} />
      </span>
      <p className="text-[13.5px] font-medium">
        {!hasProject ? "先选一个项目" : hasDecks ? "从左侧选一份演示文稿" : "这个项目还没有 pptx"}
      </p>
      <p className="max-w-[420px] text-[12px] leading-relaxed text-tertiary">
        {hasDecks
          ? "编辑器里可以直接改文字、换版式与主题，保存时按当前主题重新排版生成 pptx。"
          : "去「智能体」页让它做一页 PPT，回来这里就能改。"}
      </p>
    </div>
  );
}
