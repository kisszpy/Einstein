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
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { TaskSidebar } from "./components/TaskSidebar";
import { Composer } from "./components/Composer";
import { AssistantTurn } from "./components/AssistantTurn";
import {
  groupSessions,
  type AgentRunItem,
  type AgentStep,
  type Artifact,
  type Citation,
  type KbInfo,
  type ProjectInfo,
  type SkillInfo,
  type TaskSession,
  type ToolInfo,
  type TreeNode,
} from "./types";

/** 已归档的一轮问答（同一任务内的历史轮次） */
interface TurnView {
  task: string;
  steps: AgentStep[];
  answer: string;
  artifacts: Artifact[];
  error: string | null;
}

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
  // 技能：空 = 引擎按任务上下文自动挑选；选中 = 强制装载（pinned）
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  // 运行绑定项目：只有用户明确选择或任务产出时才设置；空 = 运行请求不带 project
  const [boundProject, setBoundProject] = useState("");
  // 文件树/预览指针：自动指向最近项目，纯浏览用，不影响运行
  const [browseProject, setBrowseProject] = useState("");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const [query, setQuery] = useState("");

  const [task, setTask] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [answer, setAnswer] = useState("");
  /** 本轮答案的检索引用（后端在 final 帧给出，支持点开溯源） */
  const [citations, setCitations] = useState<Citation[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 敏感工具等待批准：非空时展示批准条，批准后自动续跑当前任务 */
  const [approval, setApproval] = useState<{ tool: string; args: string; argsHash: string } | null>(
    null,
  );
  /** 当前任务的会话 id：追问沿用，新任务清空（由服务端分配） */
  const [sessionId, setSessionId] = useState<string | null>(null);
  /** 同一任务里已完成的更早轮次 */
  const [turns, setTurns] = useState<TurnView[]>([]);
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

  const selectBrowse = useCallback(
    (pid: string) => {
      setBrowseProject(pid);
      void loadTree(pid);
    },
    [loadTree],
  );

  // 绑定 = 用户明确指定项目随运行发送；同时文件树跟过去
  const bindProject = useCallback(
    (pid: string) => {
      setBoundProject(pid);
      if (pid) selectBrowse(pid);
    },
    [selectBrowse],
  );

  useEffect(() => {
    void fetch("/api/agent/tools")
      .then((r) => r.json())
      .then((d: { tools: ToolInfo[]; capabilities: ToolInfo[] }) => {
        setToolMeta(d);
        setCaps(d.capabilities.filter((c) => c.default).map((c) => c.id));
      });
    void fetch("/api/kb").then((r) => r.json()).then(setKbs);
    void fetch("/api/skills")
      .then((r) => r.json())
      .then((rows: (SkillInfo & { enabled?: boolean })[]) =>
        setSkills(rows.filter((s) => s.enabled !== false)),
      );
    void loadProjects();
    void loadRuns();
  }, [loadProjects, loadRuns]);

  // 文件树/预览自动指向最近更新的项目（纯浏览，不影响运行绑定）
  useEffect(() => {
    if (browseProject || projects.length === 0) return;
    const latest = [...projects].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    if (latest[0]?.id) selectBrowse(latest[0].id);
  }, [projects, browseProject, selectBrowse]);

  const openFile = useCallback(
    (filePath: string, projectId?: string) => {
      const pid = projectId || browseProject;
      if (!pid) return;
      setPreview({ tab: tabForPath(filePath), project: pid, path: filePath, url: "" });
      if (window.innerWidth < 1500) setSidebarOpen(false);
    },
    [browseProject],
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
    async (taskText: string, opts?: { resume?: boolean; message?: string }) => {
      const text = taskText.trim();
      if (!text || running) return;
      const resuming = Boolean(opts?.resume);
      // 续跑是「接着上一次的进度跑」：不清步骤、不归档，否则用户看到的是任务重来
      if (!resuming) {
        // 上一轮已有内容 → 归档到历史，让追问落在同一个任务里形成多轮
        if (submitted && (answer || steps.length > 0 || error)) {
          setTurns((prev) => [...prev, { task: submitted, steps, answer, artifacts, error }]);
        }
        setSubmitted(text);
        setSteps([]);
        setArtifacts([]);
        setAnswer("");
        setCitations([]);
      }
      setError(null);
      setApproval(null);
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
            skill_ids: selectedSkills,
            project: boundProject,
            session_id: sessionId ?? "",
            resume: resuming,
            approval_message: opts?.message ?? "",
          }),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        await readSseStream(res.body, (event, raw) => {
          switch (event) {
            case "meta": {
              // 服务端分配/回显会话 id，后续追问沿用它
              const { session_id } = raw as { session_id?: string };
              if (session_id) setSessionId(session_id);
              break;
            }
            case "step": {
              const { step } = raw as { step: AgentStep };
              if (step.kind === "approval_required") {
                // 敏感工具未批准：图已暂停在检查点，弹出批准条；批准后从断点续跑
                setApproval({
                  tool: step.tool ?? "",
                  args: step.args ?? "",
                  argsHash: step.args_hash ?? "",
                });
                break;
              }
              setSteps((prev) => [...prev, step]);
              break;
            }
            case "artifact": {
              const { artifact } = raw as { artifact: Artifact };
              setArtifacts((prev) => [...prev, artifact]);
              setPreviewToken((n) => n + 1);
              break;
            }
            case "citations": {
              const { citations: list } = raw as { citations: Citation[] };
              setCitations(list ?? []);
              break;
            }
            case "delta": {
              const { text: chunk } = raw as { text: string };
              setAnswer((prev) => prev + chunk);
              break;
            }
            case "answer_reset": {
              // 真流式：模型开始调工具，之前那段是"过程中的话"，不是最终答案
              setAnswer("");
              break;
            }
            case "done": {
              const d = raw as { project?: string; artifacts?: Artifact[] };
              const pid = d.project || boundProject;
              if (d.project && d.project !== boundProject) bindProject(d.project);
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
      submitted,
      steps,
      answer,
      artifacts,
      error,
      sessionId,
      capabilityTools,
      selectedKbs,
      selectedSkills,
      boundProject,
      loadProjects,
      loadRuns,
      loadTree,
      bindProject,
      openFile,
    ],
  );

  const submit = () => {
    const text = task;
    if (!text.trim() || running) return;
    setTask("");
    void run(text);
  };

  /** 对敏感工具作出决定：批准后从检查点续跑，拒绝则让模型换路线 */
  const approveTool = async (decision: "approve" | "reject" = "approve") => {
    if (!approval) return;
    const res = await fetch("/api/agent/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        tool: approval.tool,
        args_hash: approval.argsHash,
        decision,
      }),
    });
    if (!res.ok) {
      setError(
        res.status === 404
          ? "这次确认已过期（有效期 30 分钟），请重新发起任务。"
          : `确认失败：HTTP ${res.status}`,
      );
      setApproval(null);
      return;
    }
    setApproval(null);
    if (submitted && !running) {
      void run(submitted, {
        resume: true,
        message:
          decision === "reject"
            ? "用户拒绝了这个操作。请改用不需要它的做法；如果确实必须用它，先说明原因再请求一次。"
            : undefined,
      });
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setElapsed(Date.now() - startedAtRef.current);
  };

  const newTask = () => {
    if (running) return;
    setBoundProject(""); // 新任务不继承上一个任务的绑定
    setSessionId(null);
    setTurns([]);
    setSubmitted("");
    setSteps([]);
    setArtifacts([]);
    setAnswer("");
    setCitations([]);
    setError(null);
    setElapsed(null);
    setTask("");
  };

  /** 打开一个任务会话：更早的轮次进历史，最后一轮作为当前轮展示 */
  const selectSession = (session: TaskSession) => {
    if (running) return;
    const list = session.runs;
    const last = list[list.length - 1];
    if (!last) return;
    setSessionId(session.id);
    setTurns(
      list.slice(0, -1).map((r) => ({
        task: r.task,
        steps: r.steps ?? [],
        answer: r.answer ?? "",
        artifacts: r.artifacts ?? [],
        error: null,
      })),
    );
    setSubmitted(last.task);
    setSteps(last.steps ?? []);
    setAnswer(last.answer ?? "");
    setArtifacts(last.artifacts ?? []);
    setError(null);
    setElapsed(null);
    const pid = last.project || session.project;
    if (pid && pid !== boundProject) bindProject(pid);
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
      bindProject(info.project);
    }
  };

  const deleteProject = async () => {
    const pid = browseProject;
    if (!pid) return;
    if (!window.confirm(`删除项目「${pid}」及其全部文件？此操作不可恢复。`)) return;
    await fetch(`/api/projects/${encodeURIComponent(pid)}`, { method: "DELETE" });
    if (boundProject === pid) setBoundProject("");
    selectBrowse("");
    void loadProjects();
  };

  const deleteSession = async (session: TaskSession) => {
    const label = session.title.slice(0, 30) + (session.title.length > 30 ? "…" : "");
    const extra = session.runs.length > 1 ? `（含 ${session.runs.length} 轮对话）` : "";
    if (!window.confirm(`删除任务「${label}」${extra}？此操作不可恢复。`)) return;
    const res = await fetch(`/api/agent/runs/${encodeURIComponent(session.id)}`, { method: "DELETE" });
    if (!res.ok) return;
    setRuns((prev) => prev.filter((r) => (r.session_id || r.id) !== session.id));
    if (session.id === sessionId) newTask();
  };

  // 有新内容就贴底
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: running ? "auto" : "smooth" });
  }, [steps, answer, artifacts, running]);

  const sessions = useMemo(() => groupSessions(runs), [runs]);

  const empty = turns.length === 0 && !submitted && steps.length === 0 && !answer && !error;

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

  const title = turns[0]?.task ?? (submitted || "新任务");

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
      skills={skills}
      selectedSkills={selectedSkills}
      onToggleSkill={(id) =>
        setSelectedSkills((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]))
      }
      projects={projects}
      project={boundProject}
      onSelectProject={bindProject}
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
          sessions={sessions}
          activeSessionId={sessionId}
          onSelectSession={selectSession}
          onDeleteSession={(session) => void deleteSession(session)}
          onNewTask={newTask}
          query={query}
          onQuery={setQuery}
          project={boundProject}
          browseProject={browseProject}
          projects={projects}
          onSelectProject={bindProject}
          onCreateProject={() => void createProject()}
          onDeleteProject={() => void deleteProject()}
          onRefreshTree={() => void loadTree(browseProject)}
          onPreviewProject={() => browseProject && openProject(browseProject)}
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
              {boundProject ? `项目 · ${boundProject}` : "未绑定项目（由智能体按任务决定）"} · {caps.length} 项能力
              {selectedKbs.length > 0 && ` · ${selectedKbs.length} 个知识库`}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => browseProject && openProject(browseProject)}
              disabled={!browseProject}
              className="rounded-md px-2 py-1 text-[12px] text-secondary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              预览
            </button>
            <button
              onClick={() => (preview ? setPreview(null) : browseProject && openProject(browseProject))}
              disabled={!browseProject}
              title={preview ? "收起预览面板" : "打开预览面板"}
              className="rounded-md p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              <PanelRight size={15} strokeWidth={1.9} />
            </button>
          </div>
        </header>

        {empty ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto">
            <div className="conversation-column pb-6">
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
              <div className="conversation-column pb-4 pt-6">
                {/* 同一任务的历史轮次：完整的问答对，形成连续对话 */}
                {turns.map((turn, index) => (
                  <div
                    key={`${index}-${turn.task.slice(0, 16)}`}
                    className="mb-7 border-b border-line/70 pb-7 last:mb-6 last:border-0 last:pb-0"
                  >
                    <div className="mb-5 flex justify-end">
                      <div className="max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-tr-[6px] bg-surface-3 px-4 py-2.5 text-[14px] leading-[22px] text-primary">
                        {turn.task}
                      </div>
                    </div>
                    <AssistantTurn
                      steps={turn.steps}
                      answer={turn.answer}
                      artifacts={turn.artifacts}
                      running={false}
                      error={turn.error}
                      project={browseProject}
                      elapsedMs={null}
                      onOpenFile={(path) => openFile(path)}
                      onRerun={() => void run(turn.task)}
                    />
                  </div>
                ))}

                {/* 当前轮 */}
                {submitted && (
                  <>
                    <div className="mb-5 flex justify-end">
                      <div className="max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-tr-[6px] bg-surface-3 px-4 py-2.5 text-[14px] leading-[22px] text-primary">
                        {submitted}
                      </div>
                    </div>

                    <AssistantTurn
                      steps={steps}
                      answer={answer}
                      artifacts={artifacts}
                      citations={citations}
                      running={running}
                      error={error}
                      project={browseProject}
                      elapsedMs={elapsed}
                      onOpenFile={(path) => openFile(path)}
                      onRerun={() => void run(submitted)}
                    />
                  </>
                )}
              </div>
            </div>

            <div className="shrink-0 pb-3">
              <div className="conversation-column">
                {approval && (
                  <div className="mb-2 flex items-center justify-between gap-3 rounded-xl border border-warning/40 bg-warning/10 px-4 py-2.5">
                    <div className="min-w-0 text-[13px] text-primary">
                      <p>
                        任务已暂停：工具{" "}
                        <code className="rounded bg-surface-3 px-1 py-0.5 text-xs">
                          {approval.tool}
                        </code>{" "}
                        需要你批准后才能执行。
                      </p>
                      {approval.args && (
                        <p className="mt-0.5 truncate text-xs text-tertiary" title={approval.args}>
                          参数：{approval.args}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Button onClick={() => void approveTool("approve")}>批准并继续</Button>
                      <Button variant="ghost" onClick={() => void approveTool("reject")}>
                        拒绝
                      </Button>
                    </div>
                  </div>
                )}
                {composer}
              </div>
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
