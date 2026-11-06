/**
 * 内置浏览器：一个窗口里看三类东西——项目产物、网页、可编辑的演示文稿。
 *
 * 状态全部放在 URL 查询参数里（tab / project / path / url），刷新不丢、
 * 也方便智能体工作台和对话来源直接带参数跳进来。
 */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BookOpen,
  FileCode2,
  FolderOpen,
  Globe2,
  Loader2,
  Presentation,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ArtifactView } from "./components/ArtifactView";
import { DeckEditor } from "./components/DeckEditor";
import { FileTree } from "./components/FileTree";
import { WebView } from "./WebView";
import * as api from "./api";
import type { BrowserSource, ProjectInfo, TreeEntry } from "./types";

const TABS: { id: BrowserSource; label: string; icon: typeof Globe2 }[] = [
  { id: "artifact", label: "产物", icon: FolderOpen },
  { id: "web", label: "网页", icon: Globe2 },
  { id: "deck", label: "演示", icon: Presentation },
];

const isTab = (value: string | null): value is BrowserSource =>
  value === "artifact" || value === "web" || value === "deck";

export function BrowserPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: BrowserSource = isTab(tabParam) ? tabParam : "artifact";
  const project = params.get("project") ?? "";
  const path = params.get("path") ?? "";
  const url = params.get("url") ?? "";

  const [ticket, setTicket] = useState("");
  const [ticketError, setTicketError] = useState("");
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);

  const patch = (next: Record<string, string>) => {
    setParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        Object.entries(next).forEach(([key, value]) => {
          if (value) merged.set(key, value);
          else merged.delete(key);
        });
        return merged;
      },
      { replace: true },
    );
  };

  useEffect(() => {
    let alive = true;
    api
      .getTicket()
      .then((value) => alive && setTicket(value))
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
        if (!project && list.length === 1 && list[0]) patch({ project: list[0].id });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  }, [project]);

  const files = useMemo(() => entries.filter((entry) => entry.type === "file"), [entries]);
  const decks = useMemo(() => files.filter((file) => file.path.toLowerCase().endsWith(".pptx")), [files]);
  const activeProject = projects.find((item) => item.id === project);

  const leftList = tab === "deck" ? decks : files;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {/* 顶栏：三个来源 + 当前上下文 */}
      <div className="flex shrink-0 items-center gap-1 border-b border-line px-3">
        <div className="flex items-center gap-0.5 py-1.5">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => patch({ tab: id })}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[12.5px] transition-colors",
                tab === id
                  ? "bg-brand-soft font-medium text-brand"
                  : "text-secondary hover:bg-surface-3 hover:text-primary",
              )}
            >
              <Icon size={13} strokeWidth={1.8} />
              {label}
            </button>
          ))}
        </div>

        <span className="mx-1 h-4 w-px bg-line-strong" />

        <select
          value={project}
          aria-label="切换项目"
          onChange={(event) => patch({ project: event.target.value, path: "" })}
          className="max-w-[220px] rounded-md border border-line bg-surface-2 px-2 py-1 text-[12px] text-secondary outline-none"
        >
          <option value="">选择项目…</option>
          {projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}（{item.files}）
            </option>
          ))}
        </select>

        <span className="ml-auto flex items-center gap-2 pr-1 text-[11px] text-tertiary">
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
      </div>

      <div className="flex min-h-0 flex-1">
        {tab !== "web" && (
          <aside className="flex w-[212px] shrink-0 flex-col border-r border-line bg-surface-2">
            <div className="flex items-center justify-between px-3 pb-1 pt-2.5 text-[11px] tracking-wide text-tertiary">
              <span>{tab === "deck" ? "演示文稿" : "项目文件"}</span>
              {loadingTree && <Loader2 size={11} className="animate-spin" />}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {project ? (
                leftList.length > 0 ? (
                  tab === "deck" ? (
                    <div className="py-1">
                      {decks.map((item) => (
                        <button
                          key={item.path}
                          onClick={() => patch({ tab: "deck", path: item.path })}
                          className={cn(
                            "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]",
                            path === item.path
                              ? "bg-brand-soft text-brand"
                              : "text-secondary hover:bg-surface-3 hover:text-primary",
                          )}
                          title={item.path}
                        >
                          <Presentation size={13} className="shrink-0 opacity-70" />
                          <span className="truncate">{item.path.split("/").pop()}</span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <FileTree
                      entries={files}
                      active={path}
                      onPick={(next) =>
                        patch(
                          next.toLowerCase().endsWith(".pptx")
                            ? { tab: "deck", path: next }
                            : { tab: "artifact", path: next },
                        )
                      }
                    />
                  )
                ) : (
                  <p className="px-3 py-6 text-[12px] leading-relaxed text-tertiary">
                    {tab === "deck" ? "这个项目还没有 pptx" : "这个项目还没有文件"}
                  </p>
                )
              ) : (
                <p className="px-3 py-6 text-[12px] leading-relaxed text-tertiary">
                  先在上面选一个项目
                </p>
              )}
            </div>
          </aside>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {tab === "web" && (
            <WebView url={url} ticket={ticket} onNavigate={(next) => patch({ url: next })} />
          )}

          {tab === "artifact" &&
            (project ? (
              path ? (
                <ArtifactView
                  project={project}
                  path={path}
                  ticket={ticket}
                  onEditDeck={() => patch({ tab: "deck", path })}
                />
              ) : (
                <ProjectOverview
                  project={project}
                  projectInfo={activeProject}
                  files={files}
                  onPick={(next) => patch({ path: next })}
                />
              )
            ) : (
              <ProjectGrid
                projects={projects}
                onPick={(id) => patch({ project: id, path: "" })}
              />
            ))}

          {tab === "deck" &&
            (project && path ? (
              <DeckEditor project={project} path={path} onClose={() => patch({ path: "" })} />
            ) : (
              <EmptyDeck hasProject={Boolean(project)} hasDecks={decks.length > 0} />
            ))}
        </div>
      </div>
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
            <span className="text-[11.5px] text-tertiary">
              {item.files} 个文件 · {item.size}
            </span>
            <span className="text-[11px] text-tertiary">更新于 {item.updated_at}</span>
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
            <p className="mb-1.5 flex items-center gap-1.5 text-[11px] tracking-wide text-tertiary">
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
                    <FileCode2 size={13} className="shrink-0 text-brand" />
                  ) : file.path.endsWith(".pptx") ? (
                    <Presentation size={13} className="shrink-0 text-warning" />
                  ) : (
                    <BookOpen size={13} className="shrink-0 text-tertiary" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">
                    {file.path.split("/").pop()}
                  </span>
                  <span className="shrink-0 text-[10.5px] text-tertiary">{file.size}</span>
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
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand">
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
