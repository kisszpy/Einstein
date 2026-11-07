import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FileText,
  Globe,
  PanelLeft,
  PanelRight,
  Presentation,
  Terminal,
} from "lucide-react";
import { readSseStream } from "@/features/chat/sse-parser";
import { PreviewPanel } from "@/features/browser/PreviewPanel";
import { tabForPath, type BrowserState } from "@/features/browser/types";
import { cn } from "@/lib/utils";
import { TaskSidebar } from "./components/TaskSidebar";
import { Composer } from "./components/Composer";
import { AssistantTurn } from "./components/AssistantTurn";
import {
  type AgentRunItem,
  type AgentStep,
  type Artifact,
  type KbInfo,
  type ProjectInfo,
  type ToolInfo,
  type TreeNode,
} from "./types";

const PANEL_WIDTH_KEY = "einstein.agent.panelWidth";
const SIDEBAR_KEY = "einstein.agent.sidebarOpen";

/** 面板状态 → 独立浏览器页的深链。 */
function browserUrl(state: BrowserState): string {
  const query = new URLSearchParams();
  query.set("tab", state.tab);
  if (state.project) query.set("project", state.project);
  if (state.path) query.set("path", state.path);
  if (state.url) query.set("url", state.url);
  return `/browser?${query.toString()}`;
}

const SUGGESTIONS = [
  {
    icon: Globe,
    label: "查资料并做成 PPT",
    prompt: "查一下 2025 年大模型市场的预测数据，写一份调研并做成 PPT",
  },
  {
    icon: FileText,
    label: "写一份调研报告",
    prompt: "围绕「企业如何落地 AI 客服」写一份调研报告，保存成 report.md",
  },
  {
    icon: Terminal,
    label: "做一个能跑的网页",
    prompt: "在当前项目里做一个单页官网：标题、三个卖点、一个联系表单",
  },
  {
    icon: Presentation,
    label: "把资料整理成 7 页 PPT",
    prompt: "把当前项目里的资料整理成一份 7 页的演示文稿",
  },
];

export function AgentsPage() {
  const navigate = useNavigate();

  const [toolMeta, setToolMeta] = useState<{ tools: ToolInfo[]; capabilities: ToolInfo[] }>({
    tools: [],
    capabilities: [],
  });
  const [caps, setCaps] = useState<string[]>([]);
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [selectedKbs, setSelectedKbs] = useState<string[]>([]);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [project, setProject] = useState("");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const [query, setQuery] = useState("");

  const [task, setTask] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [answer, setAnswer] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const startedAtRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // —— 右侧预览面板（内置浏览器就地打开产物）——
  const [preview, setPreview] = useState<BrowserState | null>(null);
  const [previewToken, setPreviewToken] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    const saved = window.localStorage.getItem(SIDEBAR_KEY);
    return saved === null ? true : saved === "1";
  });
  const [panelWidth, setPanelWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem(PANEL_WIDTH_KEY));
    return Number.isFinite(saved) && saved >= 360 ? saved : 620;
  });

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? "1" : "0");
  }, [sidebarOpen]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1180px)");
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const loadProjects = useCallback(async () => {
    const res = await fetch("/api/projects");
    setProjects(await res.json());
  }, []);

  const loadRuns = useCallback(async () => {
    const res = await fetch("/api/agent/runs");
    setRuns(await res.json());
  }, []);

  const loadTree = useCallback(async (pid: string) => {
    if (!pid) {
      setTree([]);
      return;
    }
    try {
      const res = await fetch(`/api/projects/${encodeURIComponent(pid)}/tree`);
      if (!res.ok) {
        setTree([]);
        return;
      }
      const data = (await res.json()) as { entries: TreeNode[] };
      setTree(data.entries);
    } catch {
      setTree([]);
    }
  }, []);

  const selectProject = useCallback(
    (pid: string) => {
      setProject(pid);
      void loadTree(pid);
    },
    [loadTree],
  );

  // 区分「自动选中」与「手动绑定」：自动选中只服务预览入口，
  // 运行请求不带 project——文件落在哪由智能体按任务自己决定。
  const projectAutoRef = useRef(false);
  const pickProject = useCallback(
    (pid: string) => {
      projectAutoRef.current = false;
      selectProject(pid);
    },
    [selectProject],
  );

  useEffect(() => {
    void fetch("/api/agent/tools")
      .then((r) => r.json())
      .then((d: { tools: ToolInfo[]; capabilities: ToolInfo[] }) => {
        setToolMeta(d);
        setCaps(d.capabilities.filter((c) => c.default).map((c) => c.id));
      });
    void fetch("/api/kb").then((r) => r.json()).then(setKbs);
    void loadProjects();
    void loadRuns();
  }, [loadProjects, loadRuns]);

  // 还没选过项目就自动选最近更新的一个，预览入口不用干等用户手动选
  useEffect(() => {
    if (project || projects.length === 0) return;
    const latest = [...projects].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    if (latest[0]?.id) {
      projectAutoRef.current = true;
      selectProject(latest[0].id);
    }
  }, [projects, project, selectProject]);

  const openFile = useCallback(
    (filePath: string, projectId?: string) => {
      const pid = projectId || project;
      if (!pid) return;
      setPreview({ tab: tabForPath(filePath), project: pid, path: filePath, url: "" });
      if (window.innerWidth < 1500) setSidebarOpen(false);
    },
    [project],
  );

  const openProject = useCallback(
    (pid: string) => {
      setPreview({ tab: "artifact", project: pid, path: "", url: "" });
      if (window.innerWidth < 1500) setSidebarOpen(false);
    },
    [],
  );

  const patchPreview = useCallback((patch: Partial<BrowserState>) => {
    setPreview((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const startResize = (event: React.MouseEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = panelWidth;
    const move = (ev: MouseEvent) => {
      const next = Math.min(Math.max(startWidth + (startX - ev.clientX), 380), window.innerWidth - 460);
      setPanelWidth(next);
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setPanelWidth((width) => {
        window.localStorage.setItem(PANEL_WIDTH_KEY, String(Math.round(width)));
        return width;
      });
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const capabilityTools = useMemo(
    () =>
      caps.flatMap((capId) => toolMeta.capabilities.find((c) => c.id === capId)?.tools ?? []),
    [caps, toolMeta.capabilities],
  );

  const run = useCallback(
    async (taskText: string) => {
      const text = taskText.trim();
      if (!text || running) return;
      setSubmitted(text);
      setActiveRunId(null);
      setSteps([]);
      setArtifacts([]);
      setAnswer("");
      setError(null);
      setElapsed(null);
      setRunning(true);
      startedAtRef.current = Date.now();
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const res = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            task: text,
            tool_ids: capabilityTools,
            kb_ids: selectedKbs,
            project: projectAutoRef.current ? "" : project,
          }),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        await readSseStream(res.body, (event, raw) => {
          switch (event) {
            case "step": {
              const { step } = raw as { step: AgentStep };
              setSteps((prev) => [...prev, step]);
              break;
            }
            case "artifact": {
              const { artifact } = raw as { artifact: Artifact };
              setArtifacts((prev) => [...prev, artifact]);
              setPreviewToken((n) => n + 1);
              break;
            }
            case "delta": {
              const { text: chunk } = raw as { text: string };
              setAnswer((prev) => prev + chunk);
              break;
            }
            case "done": {
              const d = raw as { project?: string; artifacts?: Artifact[] };
              const pid = d.project || project;
              if (d.project && d.project !== project) pickProject(d.project);
              else if (d.project) void loadTree(d.project);
              setPreviewToken((n) => n + 1);
              void loadProjects();
              // 跑完自动把产物摊开——和 WorkBuddy 一样，不用用户再点一次
              const produced = d.artifacts ?? [];
              if (produced.length > 0 && pid) {
                const pick =
                  [...produced].reverse().find((a) => a.path.toLowerCase().endsWith(".pptx")) ??
                  produced[produced.length - 1];
                if (pick) openFile(pick.path, pid);
              }
              break;
            }
            case "error":
              setError((raw as { message: string }).message);
              break;
          }
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(`请求失败：${(err as Error).message}`);
        }
      } finally {
        abortRef.current = null;
        setRunning(false);
        setElapsed(Date.now() - startedAtRef.current);
        void loadRuns();
      }
    },
    [
      running,
      capabilityTools,
      selectedKbs,
      project,
      loadProjects,
      loadRuns,
      loadTree,
      selectProject,
      openFile,
    ],
  );

  const submit = () => {
    const text = task;
    if (!text.trim() || running) return;
    setTask("");
    void run(text);
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setElapsed(Date.now() - startedAtRef.current);
  };

  const newTask = () => {
    if (running) return;
    setSubmitted("");
    setSteps([]);
    setArtifacts([]);
    setAnswer("");
    setError(null);
    setElapsed(null);
    setActiveRunId(null);
    setTask("");
  };

  const selectRun = (item: AgentRunItem) => {
    if (running) return;
    setSubmitted(item.task);
    setSteps(item.steps ?? []);
    setAnswer(item.answer ?? "");
    setArtifacts(item.artifacts ?? []);
    setError(null);
    setElapsed(null);
    setActiveRunId(item.id);
    if (item.project && item.project !== project) pickProject(item.project);
  };

  const createProject = async () => {
    const name = window.prompt("项目名称（字母/数字/中文/连字符）");
    if (!name?.trim()) return;
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), template: "blank" }),
    });
    if (res.ok) {
      const info = await res.json();
      await loadProjects();
      pickProject(info.project);
    }
  };

  const deleteProject = async () => {
    if (!project) return;
    if (!window.confirm(`删除项目「${project}」及其全部文件？此操作不可恢复。`)) return;
    await fetch(`/api/projects/${encodeURIComponent(project)}`, { method: "DELETE" });
    projectAutoRef.current = false;
    selectProject("");
    void loadProjects();
  };

  // 有新内容就贴底
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: running ? "auto" : "smooth" });
  }, [steps, answer, artifacts, running]);

  const empty = !submitted && steps.length === 0 && !answer && !error;

  useEffect(() => {
    if (!empty) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target as HTMLElement)?.closest("textarea, input")) {
        e.preventDefault();
        document.querySelector<HTMLTextAreaElement>("[data-agent-composer]")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [empty]);

  const title = submitted ? submitted : "新任务";

  const composer = (
    <Composer
      value={task}
      onChange={setTask}
      onSubmit={submit}
      onStop={stop}
      running={running}
      capabilities={toolMeta.capabilities}
      caps={caps}
      onToggleCap={(id) =>
        setCaps((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]))
      }
      kbs={kbs}
      selectedKbs={selectedKbs}
      onToggleKb={(id) =>
        setSelectedKbs((prev) => (prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id]))
      }
      projects={projects}
      project={project}
      onSelectProject={pickProject}
      onCreateProject={() => void createProject()}
      autoFocus={empty}
    />
  );

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden bg-page">
      <div
        className={cn(
          "shrink-0 overflow-hidden transition-[width] duration-200 ease-out",
          sidebarOpen ? "w-[264px]" : "w-0",
        )}
      >
        <TaskSidebar
          runs={runs}
          activeRunId={activeRunId}
          onSelectRun={selectRun}
          onNewTask={newTask}
          query={query}
          onQuery={setQuery}
          project={project}
          projects={projects}
          onSelectProject={pickProject}
          onCreateProject={() => void createProject()}
          onDeleteProject={() => void deleteProject()}
          onRefreshTree={() => void loadTree(project)}
          onPreviewProject={() => project && openProject(project)}
          tree={tree}
          onOpenFile={(path) => openFile(path)}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col bg-surface-1">
        {/* 顶栏：标题 + 右侧操作 */}
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            title={sidebarOpen ? "收起任务栏" : "展开任务栏"}
            className="rounded-md p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary"
          >
            <PanelLeft size={15} strokeWidth={1.9} />
          </button>

          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-primary">{title}</div>
            <div className="truncate text-[11px] text-tertiary">
              {project ? `项目 · ${project}` : "未绑定项目"} · {caps.length} 项能力
              {selectedKbs.length > 0 && ` · ${selectedKbs.length} 个知识库`}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => project && openProject(project)}
              disabled={!project}
              className="rounded-md px-2 py-1 text-[12px] text-secondary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              预览
            </button>
            <button
              onClick={() => (preview ? setPreview(null) : project && openProject(project))}
              disabled={!project}
              title={preview ? "收起预览面板" : "打开预览面板"}
              className="rounded-md p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              <PanelRight size={15} strokeWidth={1.9} />
            </button>
          </div>
        </header>

        {empty ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6">
            <div className="w-full max-w-[720px] pb-6">
              <div className="mb-6 flex flex-col items-center text-center">
                <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-soft text-[17px] font-semibold text-brand-deep">
                  E
                </span>
                <h1 className="text-[22px] font-medium tracking-tight text-primary">
                  今天想做点什么？
                </h1>
                <p className="mt-1.5 text-[13px] text-secondary">
                  把任务交给智能体：自己查资料、写文件、跑代码，直接产出可下载的成果
                </p>
              </div>

              <div className="mb-5 flex flex-wrap justify-center gap-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s.label}
                    onClick={() => setTask(s.prompt)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 py-1.5 text-[12px] text-secondary transition-colors duration-150 hover:border-line-strong hover:text-primary"
                  >
                    <s.icon size={12.5} strokeWidth={1.9} className="text-tertiary" />
                    {s.label}
                  </button>
                ))}
              </div>

              {composer}
            </div>
          </div>
        ) : (
          <>
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-[848px] px-6 pb-4 pt-6">
                {/* 用户消息 */}
                <div className="mb-5 flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-tr-[6px] bg-surface-3 px-4 py-2.5 text-[14px] leading-[22px] text-primary">
                    {submitted}
                  </div>
                </div>

                <AssistantTurn
                  steps={steps}
                  answer={answer}
                  artifacts={artifacts}
                  running={running}
                  error={error}
                  project={project}
                  elapsedMs={elapsed}
                  onOpenFile={(path) => openFile(path)}
                  onRerun={() => void run(submitted)}
                />
              </div>
            </div>

            <div className="shrink-0 px-6 pb-3">
              <div className="mx-auto w-full max-w-[848px]">{composer}</div>
            </div>
          </>
        )}
      </div>

      {/* 右侧预览面板：内置浏览器就地打开产物，不用离开当前任务 */}
      {preview && (
        <>
          {!narrow && (
            <div
              onMouseDown={startResize}
              title="拖动调整宽度"
              className="group relative z-10 w-1 shrink-0 cursor-col-resize bg-line transition-colors hover:bg-brand/50"
            >
              <span className="absolute inset-y-0 -left-1.5 -right-1.5" />
            </div>
          )}
          <aside
            aria-label="产物预览面板"
            className={cn(
              "flex min-h-0 flex-col bg-surface-1",
              narrow ? "absolute inset-0 z-30" : "shrink-0 border-l border-line",
            )}
            style={narrow ? undefined : { width: panelWidth }}
          >
            <PreviewPanel
              value={preview}
              onChange={patchPreview}
              onClose={() => setPreview(null)}
              onMaximize={() => navigate(browserUrl(preview))}
              refreshToken={previewToken}
            />
          </aside>
        </>
      )}
    </div>
  );
}
