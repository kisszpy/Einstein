/**
 * 资产页的工作台本体：产物 / 网页 / 演示三源。
 *
 * 抽成受控组件是为了两个容器共用一份实现——
 * 独立页 /assets（状态放 URL）和智能体页右侧的预览面板（状态放组件内）。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import {
  BookOpen,
  ChevronDown,
  Code2,
  FileCode2,
  FolderOpen,
  Globe2,
  LayoutTemplate,
  Loader2,
  PanelLeft,
  Presentation,
  Projector,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ArtifactView } from "./components/ArtifactView";
import { DeckCraftStage } from "./components/DeckCraftStage";
import { DeckEditor } from "./components/DeckEditor";
import { confirmDialog } from "@/lib/confirm";
import { Trash2 } from "lucide-react";
import { FileTree } from "./components/FileTree";
import { ThemePackPanel } from "./components/ThemePackPanel";
import { WebView } from "./WebView";
import * as api from "./api";
import { type BrowserState, type ProjectInfo, type TreeEntry } from "./types";

const TABS = [
  { id: "artifact" as const, label: "产物", icon: FolderOpen },
  { id: "web" as const, label: "网页", icon: Globe2 },
  { id: "deck" as const, label: "演示", icon: Presentation },
  { id: "templates" as const, label: "模板", icon: LayoutTemplate },
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
  const [railOpen, setRailOpen] = useState(() => !compact && window.innerWidth >= 768);
  const [treeTick, setTreeTick] = useState(0);
  /** pptx 的两种呈现：放映级（DeckCraft，默认）与结构化（服务端按主题重排）。
   *  默认给放映级——用户要的是「所见即所得的漂亮演示」。 */
  const [deckMode, setDeckMode] = useState<"craft" | "dsl">("craft");

  /** 文件栏宽度：写死 212 时，com/shop/common/api/Result.java 这种长路径
   *  会被截成「Resu…」，而工程类项目恰恰全是这种深度。改成可拖，并把用户
   *  最后一次的宽度记在本地——同一个人的屏幕和习惯不会每次都变。 */
  const RAIL_MIN = 168;
  const railDefault = compact ? 216 : 248;
  const railKey = `assets.railWidth${compact ? ".compact" : ""}`;
  const [railWidth, setRailWidth] = useState<number>(() => {
    try {
      const saved = Number(window.localStorage.getItem(railKey));
      if (Number.isFinite(saved) && saved >= RAIL_MIN) return saved;
    } catch {
      // 无痕模式下 localStorage 会抛，静默回落到默认宽度
    }
    return railDefault;
  });
  const railWidthRef = useRef(railWidth);
  const railBoxRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startW: number; max: number } | null>(null);

  useEffect(() => {
    railWidthRef.current = railWidth;
  }, [railWidth]);

  const rememberRail = useCallback(
    (value: number) => {
      try {
        window.localStorage.setItem(railKey, String(value));
      } catch {
        // 存不下不影响本次使用
      }
    },
    [railKey],
  );

  /** 拖动右边缘改宽度：上限取容器的 62%，保证右侧预览区还有可用面积。 */
  const onRailResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const max = Math.max(
      RAIL_MIN,
      Math.round((railBoxRef.current?.clientWidth ?? window.innerWidth) * 0.62),
    );
    dragRef.current = { startX: event.clientX, startW: railWidthRef.current, max };
    event.currentTarget.setPointerCapture(event.pointerId);
    // 拖动时锁住选中与光标：否则扫过文字会变成蓝色高亮，手感很差
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  const onRailResizeMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const next = Math.min(drag.max, Math.max(RAIL_MIN, drag.startW + (event.clientX - drag.startX)));
    setRailWidth(next);
  };

  const onRailResizeEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    rememberRail(railWidthRef.current);
  };

  /** 删除整个项目：连带目录内所有文件，二次确认要写清范围。 */
  const handleDeleteProject = async (id: string, name: string) => {
    const ok = await confirmDialog({
      title: "删除项目",
      message: `「${name}」以及它里面的全部文件都会被永久删除，无法恢复。`,
      confirmLabel: "删除项目",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteProject(id);
      void api.listProjects().then(setProjects).catch(() => undefined);
      if (project === id) onChange({ project: "", path: "" });
      setTreeTick((n) => n + 1);
    } catch (err) {
      void confirmDialog({
        title: "删除失败",
        message: err instanceof Error ? err.message : "请稍后再试",
        confirmLabel: "知道了",
      });
    }
  };

  /** 删除一个文件：确认 → 调接口 → 刷新树与项目列表 → 选中项被删则清空。 */
  const handleDelete = async (target: string) => {
    if (!project) return;
    const ok = await confirmDialog({
      title: "删除文件",
      message: `${target.split("/").pop()} 将被永久删除，无法恢复。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteFile(project, target);
      setTreeTick((n) => n + 1);
      void api.listProjects().then(setProjects).catch(() => undefined);
      if (path === target) onChange({ path: "" });
    } catch (err) {
      void confirmDialog({
        title: "删除失败",
        message: err instanceof Error ? err.message : "请稍后再试",
        confirmLabel: "知道了",
      });
    }
  };

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
    // 浏览页只展示产物：后端按白名单后缀过滤，源码/依赖/解包残留不出现
    api
      .projectArtifacts(project)
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
  const showRail = tab !== "web" && tab !== "templates" && railOpen;

  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col", className)}>
      {/* 顶栏：三个来源 + 当前上下文 */}
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line px-2.5">
        <div className="flex min-w-0 shrink items-center gap-0.5 overflow-x-auto py-1.5 [scrollbar-width:none]">
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
                "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-[12.5px] transition-colors",
                tab === id
                  ? "bg-brand-soft font-medium text-brand-ink "
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

        {tab !== "web" && tab !== "templates" && (
          <button
            onClick={() => setRailOpen((prev) => !prev)}
            title={railOpen ? "收起文件栏" : "展开文件栏"}
            aria-label={railOpen ? "收起文件栏" : "展开文件栏"}
            aria-expanded={railOpen}
            className={cn(
              "rounded-md p-1.5 transition-colors",
              compact ? "" : "md:hidden",
              railOpen ? "text-brand-ink " : "text-tertiary hover:bg-surface-3 hover:text-secondary",
            )}
          >
            <PanelLeft size={13} strokeWidth={1.8} />
          </button>
        )}

        <div className="relative shrink-0">
          <select
            value={project}
            aria-label="切换项目"
            onChange={(event) => onChange({ project: event.target.value, path: "" })}
            className="h-8 w-[142px] appearance-none truncate rounded-lg border border-line-control bg-surface-1 pl-2.5 pr-7 text-[12.5px] text-secondary outline-none transition-colors hover:border-line-strong focus-visible:border-brand sm:w-[186px]"
          >
            <option value="">选择项目…</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.artifacts}）
              </option>
            ))}
          </select>
          <ChevronDown
            size={13}
            strokeWidth={2}
            className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-tertiary"
          />
        </div>

        {!compact && (
          <span className="ml-auto flex items-center gap-2 pr-1 text-[12px] text-tertiary">
            {activeProject && tab !== "web" && (
              <>
                <span>{activeProject.artifacts} 个产物</span>
                <span className="h-3 w-px bg-line" />
                <span>{activeProject.size}</span>
                <span className="h-3 w-px bg-line" />
              </>
            )}
            <span className="flex items-center gap-1.5">
              <span
                className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  ticket ? "bg-success" : ticketError ? "bg-danger" : "bg-line-strong",
                )}
              />
              {ticket ? "预览服务正常" : ticketError ? "预览服务未就绪" : "连接中"}
            </span>
          </span>
        )}

        <div className={cn("flex items-center gap-1", compact ? "ml-auto pr-0.5" : "")}>
          {headerRight}
        </div>
      </div>

      <div ref={railBoxRef} className="relative flex min-h-0 flex-1">
        {showRail && (
          <>
            <div
              className="absolute inset-0 z-20 bg-black/30 md:hidden"
              onClick={() => setRailOpen(false)}
            />
            <aside
              className="relative flex shrink-0 flex-col border-r border-line bg-surface-2 max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-[var(--shadow-pop)]"
              style={{ width: railWidth }}
            >
            <div className="flex items-center justify-between px-2.5 pb-1 pt-2 text-[12px] tracking-wide text-tertiary">
              <span className="flex items-center gap-1">
                {tab === "deck" ? "演示文稿" : "产物"}
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
                    <DeckList
                      decks={decks}
                      active={path}
                      onPick={(next) => onChange({ tab: "deck", path: next })}
                      onDelete={handleDelete}
                    />
                  ) : (
                    <FileTree
                      entries={files}
                      active={path}
                      onDelete={handleDelete}
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
                    {tab === "deck" ? "这个项目还没有 pptx" : "这个项目还没有产物"}
                  </p>
                )
              ) : (
                <div className="px-3 py-8 text-center">
                  <FolderOpen size={18} className="mx-auto mb-2 text-tertiary" strokeWidth={1.6} />
                  <p className="text-[12px] leading-relaxed text-tertiary">
                    在顶部选一个项目
                    <br />
                    即可查看产物文件
                  </p>
                </div>
              )}
            </div>
            {/* 拖拽手柄：命中区 6px、视觉 2px，鼠标扫过才亮，平时不抢注意力。
                双击恢复默认宽度——拖坏了能一键回到可用状态。 */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="拖动调整文件栏宽度，双击恢复默认"
              onPointerDown={onRailResizeStart}
              onPointerMove={onRailResizeMove}
              onPointerUp={onRailResizeEnd}
              onPointerCancel={onRailResizeEnd}
              onDoubleClick={() => {
                setRailWidth(railDefault);
                rememberRail(railDefault);
              }}
              title="拖动调整宽度，双击恢复默认"
              className="absolute -right-[3px] top-0 z-10 h-full w-[6px] cursor-col-resize touch-none bg-transparent transition-colors hover:bg-brand/25 active:bg-brand/40"
            />
          </aside>
          </>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {tab === "web" && (
            <WebView url={url} ticket={ticket} onNavigate={(next) => onChange({ url: next })} />
          )}

          {tab === "templates" && <ThemePackPanel />}

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
                  onDelete={handleDelete}
                />
              )
            ) : (
              <ProjectGrid
                projects={projects}
                onPick={(id) => onChange({ project: id, path: "" })}
                onDelete={handleDeleteProject}
              />
            ))}

          {tab === "deck" &&
            (project && path ? (
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                {/* 呈现方式：pptx 默认放映级（真母版/版式/动画），要批量改文字再切结构化 */}
                <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-2.5 py-1">
                  <div className="flex items-center gap-0.5 rounded-md bg-surface-3 p-0.5">
                    {(
                      [
                        {
                          id: "craft" as const,
                          label: "放映级",
                          icon: Projector,
                          hint: "真母版与版式、动画、放映；编辑器内 Ctrl+S 保存回传 pptx",
                        },
                        {
                          id: "dsl" as const,
                          label: "结构化",
                          icon: Code2,
                          hint: "改文字与版式，保存时由服务端按主题重新排版",
                        },
                      ] satisfies { id: "craft" | "dsl"; label: string; icon: typeof Projector; hint: string }[]
                    ).map(({ id, label, icon: Icon, hint }) => (
                      <button
                        key={id}
                        onClick={() => setDeckMode(id)}
                        title={hint}
                        aria-pressed={deckMode === id}
                        className={cn(
                          "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11.5px] transition-colors",
                          deckMode === id
                            ? "bg-surface-1 font-medium text-primary shadow-sm"
                            : "text-tertiary hover:text-primary",
                        )}
                      >
                        <Icon size={11} strokeWidth={2} />
                        {label}
                      </button>
                    ))}
                  </div>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-tertiary">{path}</span>
                </div>
                <div className="min-h-0 flex-1">
                  {deckMode === "craft" ? (
                    <DeckCraftStage
                      project={project}
                      path={path}
                      ticket={ticket}
                      onSaved={() => setTreeTick((n) => n + 1)}
                    />
                  ) : (
                    <DeckEditor
                      project={project}
                      path={path}
                      compact={compact}
                      onClose={() => onChange({ path: "" })}
                    />
                  )}
                </div>
              </div>
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
  onDelete,
}: {
  decks: TreeEntry[];
  active: string;
  onPick: (path: string) => void;
  onDelete?: (path: string) => void;
}) {
  return (
    <div className="py-1">
      {decks.map((item) => (
        <div
          key={item.path}
          className={cn(
            "group flex w-full items-center",
            active === item.path ? "bg-brand-soft" : "hover:bg-surface-3",
          )}
        >
          <button
            onClick={() => onPick(item.path)}
            className={cn(
              "flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left text-[12.5px]",
              active === item.path ? "text-brand-ink" : "text-secondary group-hover:text-primary",
            )}
            title={item.path}
          >
            <Presentation size={13} className="shrink-0 opacity-70" />
            <span className="truncate">{item.path.split("/").pop()}</span>
          </button>
          {onDelete && (
            <button
              type="button"
              onClick={() => onDelete(item.path)}
              title="删除这份演示文稿"
              aria-label={`删除 ${item.path.split("/").pop()}`}
              className="mr-1.5 hidden shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger group-hover:block focus:block"
            >
              <Trash2 size={12} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function ProjectGrid({
  projects,
  onPick,
  onDelete,
}: {
  projects: ProjectInfo[];
  onPick: (id: string) => void;
  onDelete?: (id: string, name: string) => void;
}) {
  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <h2 className="text-[14px] font-medium">选择一个项目</h2>
      <p className="mt-1 text-[12px] text-tertiary">
        智能体创建的项目都在这里，点进去就能预览产物、改文件。
      </p>
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {projects.map((item) => (
          <div key={item.id} className="group relative">
            <button
              onClick={() => onPick(item.id)}
              className="flex w-full flex-col items-start gap-2 rounded-xl border border-line bg-surface-1 p-4 text-left transition-colors hover:border-brand/40 hover:bg-brand-soft/40"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-3 text-secondary group-hover:bg-brand-soft group-hover:text-brand-ink">
                <FolderOpen size={15} strokeWidth={1.8} />
              </span>
              <span className="w-full truncate text-[13px] font-medium">{item.name}</span>
              <span className="text-[12px] text-tertiary">
                {item.artifacts} 个产物 · {item.size}
              </span>
              <span className="text-[12px] text-tertiary">更新于 {item.updated_at}</span>
            </button>
            {onDelete && (
              <button
                type="button"
                onClick={() => onDelete(item.id, item.name)}
                title="删除整个项目"
                aria-label={`删除项目 ${item.name}`}
                className="absolute right-2 top-2 hidden rounded-md p-1.5 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger group-hover:block focus:block"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
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
  onDelete,
}: {
  project: string;
  projectInfo?: ProjectInfo;
  files: TreeEntry[];
  onPick: (path: string) => void;
  onDelete?: (path: string) => void;
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
        {files.length} 个产物 · {projectInfo?.size || "—"} · 从左侧或下面的列表打开
      </p>
      <div className="mt-5 space-y-4">
        {Object.entries(grouped).map(([dir, list]) => (
          <div key={dir}>
            <p className="mb-1.5 flex items-center gap-1.5 text-[12px] tracking-wide text-tertiary">
              <FolderOpen size={11} /> {dir}
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {list.map((file) => (
                <div
                  key={file.path}
                  className="group flex items-center rounded-lg border border-line bg-surface-1 hover:border-brand/40 hover:bg-brand-soft/30"
                >
                  <button
                    onClick={() => onPick(file.path)}
                    className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left"
                    title={file.path}
                  >
                    {file.path.endsWith(".html") ? (
                      <FileCode2 size={13} className="shrink-0 text-brand-ink " />
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
                  {onDelete && (
                    <button
                      type="button"
                      onClick={() => onDelete(file.path)}
                      title="删除这个文件"
                      aria-label={`删除 ${file.path.split("/").pop()}`}
                      className="mr-1.5 hidden shrink-0 rounded p-1.5 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger group-hover:block focus:block"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
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
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand-ink ">
        <Presentation size={20} strokeWidth={1.6} />
      </span>
      <p className="text-[13.5px] font-medium">
        {!hasProject ? "先选一个项目" : hasDecks ? "从左侧选一份演示文稿" : "这个项目还没有 pptx"}
      </p>
      <p className="max-w-[420px] text-[12px] leading-relaxed text-tertiary">
        {hasDecks
          ? "选中后默认用放映级编辑器打开：真母版与版式、动画、放映模式，Ctrl+S 直接写回这份 pptx；要批量改文字可切「结构化」。"
          : "去「智能体」页让它做一页 PPT，回来这里就能改。"}
      </p>
    </div>
  );
}
