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
import { BrandMark } from "@/components/BrandMark";
import { cn } from "@/lib/utils";
import { TaskSidebar } from "./components/TaskSidebar";
import { Composer } from "./components/Composer";
import {
  SaveTemplateDialog,
  TemplateBar,
  type TaskTemplate,
} from "./components/TemplateBar";
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
} from "./types";
import { confirmDialog, promptDialog } from "@/lib/confirm";

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

/** 面板状态 → 独立资产页的深链。 */
function browserUrl(state: BrowserState): string {
  const query = new URLSearchParams();
  query.set("tab", state.tab);
  if (state.project) query.set("project", state.project);
  if (state.path) query.set("path", state.path);
  if (state.url) query.set("url", state.url);
  return `/assets?${query.toString()}`;
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
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const [query, setQuery] = useState("");

  const [task, setTask] = useState("");
  /** 任务模板：提示"这类活怎么发起"的起步参数，不固化执行步骤 */
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [saveOpen, setSaveOpen] = useState(false);
  /** 模板带来的工具挂载：与手动勾选的能力合并后一起发给引擎 */
  const [templateTools, setTemplateTools] = useState<string[]>([]);
  const [submitted, setSubmitted] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [answer, setAnswer] = useState("");
  /** 本轮答案的检索引用（后端在 final 帧给出，支持点开溯源） */
  const [citations, setCitations] = useState<Citation[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 敏感工具等待批准：非空时展示批准条，批准后自动续跑当前任务 */
  const [approval, setApproval] = useState<{
    tool: string;
    args: string;
    argsHash: string;
    /** e2b / container / host：决定文案说的风险等级 */
    env: string;
  } | null>(null);
  /** 当前任务的会话 id：追问沿用，新任务清空（由服务端分配） */
  const [sessionId, setSessionId] = useState<string | null>(null);
  /** 同一任务里已完成的更早轮次 */
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const startedAtRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  /** 本轮运行的 run_id：点「停止」时用它通知后端真正取消，而不只是断开 SSE */
  const runIdRef = useRef<string | null>(null);
  /**
   * 后台还在跑的运行：sessionId → runId。
   *
   * 执行本来就在服务端的独立 task 里，前端断开 SSE 只是「我不看了」，任务
   * 照跑。所以切走一个会话时把它记在这里，切回来再接上——用户不必等一个
   * 任务跑完才能去看别的任务。
   */
  const liveRunsRef = useRef<Record<string, string>>({});
  /** 侧栏标记用（ref 变了不会触发渲染，这里存一份快照） */
  const [liveSessions, setLiveSessions] = useState<string[]>([]);
  /** 上一次收到真实事件的时间：给「重连后毫无动静」的流加一道自我超时 */
  const lastEventAtRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const syncLive = useCallback(() => {
    setLiveSessions(Object.keys(liveRunsRef.current));
  }, []);

  /** 登记「这个会话有一个还在后台跑的任务」 */
  const markLive = useCallback(
    (sid: string, runId: string) => {
      if (!sid || !runId) return;
      liveRunsRef.current[sid] = runId;
      syncLive();
    },
    [syncLive],
  );

  /** 注销：正常结束或用户主动停止时调用 */
  const clearLive = useCallback(
    (runId: string | null) => {
      if (!runId) return;
      let dirty = false;
      for (const [sid, rid] of Object.entries(liveRunsRef.current)) {
        if (rid === runId) {
          delete liveRunsRef.current[sid];
          dirty = true;
        }
      }
      if (dirty) syncLive();
    },
    [syncLive],
  );

  // —— 右侧预览面板（内置浏览器就地打开产物）——
  const [preview, setPreview] = useState<BrowserState | null>(null);
  const [previewToken, setPreviewToken] = useState(0);
  const [narrow, setNarrow] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    const saved = window.localStorage.getItem(SIDEBAR_KEY);
    return saved === null ? window.innerWidth >= 768 : saved === "1";
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

  const selectBrowse = useCallback((pid: string) => {
    setBrowseProject(pid);
  }, []);

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
  const loadTemplates = useCallback(async () => {
    try {
      const res = await fetch("/api/templates");
      if (!res.ok) return;
      setTemplates((await res.json()) as TaskTemplate[]);
    } catch {
      // 模板加载失败不该挡住发任务：这里静默，页面上就是没有模板条
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

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

  /** 事件归一：首次连接与刷新后重连共用同一套处理，避免两条路径行为漂移 */
  const applyEvent = useCallback(
    (event: string, raw: unknown) => {
      // 心跳（后端每 15 秒的注释行）到不了这里；能到就说明任务真的在动。
      // 重连后长时间没有事件，靠这个时间戳让读者自己断开，而不是永远转圈。
      lastEventAtRef.current = Date.now();
      switch (event) {
        case "meta": {
          // 服务端分配/回显会话 id，后续追问沿用它
          const { session_id, run_id } = raw as { session_id?: string; run_id?: string };
          if (session_id) setSessionId(session_id);
          // run_id 是「停止」的抓手：后端按它撤销执行任务
          if (run_id) runIdRef.current = run_id;
          // 记下「这个会话正在后台跑」：切走再切回来时据此重新接上
          if (session_id && run_id) markLive(session_id, run_id);
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
              env: step.env ?? "",
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
          // 这一轮真的跑完了：不再需要在跑标记
          clearLive(runIdRef.current);
          const pid = d.project || boundProject;
          if (d.project && d.project !== boundProject) bindProject(d.project);
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
          clearLive(runIdRef.current);
          setError((raw as { message: string }).message);
          break;
      }
    },
    [boundProject, bindProject, loadProjects, openFile, markLive, clearLive],
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
      // 本轮自己的 run_id：不能等到 finally 再读 runIdRef —— 用户中途切走
      // 又切回来时，那里已经被新一条运行覆盖了，会清错人。
      let myRunId: string | null = null;
      try {
        const res = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            task: text,
            tool_ids: Array.from(new Set([...capabilityTools, ...templateTools])),
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
          if (event === "meta") {
            const { run_id } = raw as { run_id?: string };
            if (run_id) myRunId = run_id;
          }
          applyEvent(event, raw);
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(`请求失败：${(err as Error).message}`);
        }
      } finally {
        const aborted = controller.signal.aborted;
        // 只有「这条流还是当前那条」才允许收尾状态。用户切走再切回来时，
        // 新的 attach 已经换了 controller；旧流的 finally 要是照样把
        // running 关掉，新流就永远显示不出「运行中」，点停止也 abort 不到它。
        if (abortRef.current === controller) {
          abortRef.current = null;
          setRunning(false);
          setElapsed(Date.now() - startedAtRef.current);
        }
        // 被「切去看别的任务」打断的那条还在后台跑，保留在跑标记；
        // 正常收尾（含服务端报错）才注销。
        if (!aborted) clearLive(myRunId);
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
      templateTools,
      selectedKbs,
      selectedSkills,
      boundProject,
      loadProjects,
      loadRuns,
      bindProject,
      openFile,
      applyEvent,
      clearLive,
    ],
  );

  /** 刷新/切回后重新挂上正在跑的那一轮：登记簿只增，从第 0 帧重放不会看到半个答案 */
  const attach = useCallback(
    async (runId: string, sessionHint?: string) => {
      setRunning(true);
      setError(null);
      // 重连是从第 0 条重放，本地必须清干净——否则「刷新后接回」会看到两遍步骤。
      // 服务端可能落在另一个副本上（事件从 run_events 表重放），行为与本地一致。
      setSteps([]);
      setArtifacts([]);
      setAnswer("");
      setCitations([]);
      setApproval(null);
      // 重连时也把 run_id 记下：否则刷新后点「停止」会因为没有 id 而只断流
      runIdRef.current = runId;
      if (sessionHint) markLive(sessionHint, runId);
      startedAtRef.current = Date.now();
      lastEventAtRef.current = Date.now();
      const controller = new AbortController();
      abortRef.current = controller;

      // 兜底：一条「永远不收尾」的流（进程被杀后留在库里的死记录）会让 running
      // 一直为真，整个页面又回到「切不动」的状态。这里超过一分钟没有任何真实
      // 事件就自己断开——心跳不算事件，服务端每 15 秒会发一次，所以真活着的流
      // 不会触发。
      const watchdog = window.setInterval(() => {
        if (Date.now() - lastEventAtRef.current < 60_000) return;
        window.clearInterval(watchdog);
        // 超时说明这条运行确实死了（服务端每 15 秒还有心跳，能等到这里就是
        // 连事件都不再产出）。标记要一并清掉，否则侧栏永远挂着「运行中」。
        clearLive(runId);
        controller.abort();
        setError("这条运行已经很久没有进展，已停止跟随。任务可能已中断，可以重新发起。");
      }, 5_000);

      try {
        const res = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/stream`, {
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        await readSseStream(res.body, applyEvent);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(`重新接回失败：${(err as Error).message}`);
        }
      } finally {
        window.clearInterval(watchdog);
        // 同 run()：只有自己还是当前那条流时才收尾状态，别把新接上的流关掉
        if (abortRef.current === controller) {
          abortRef.current = null;
          setRunning(false);
          setElapsed(Date.now() - startedAtRef.current);
        }
        // 主动切走的流不注销（任务还在后台跑）；真正结束或超时才注销
        if (!controller.signal.aborted) clearLive(runId);
        void loadRuns();
      }
    },
    [applyEvent, loadRuns, markLive, clearLive],
  );

  // 只在首次挂载时探测一次：页面刷新后任务还在后台跑，这里把读者重新挂上
  const attachedRef = useRef(false);
  useEffect(() => {
    if (attachedRef.current) return;
    attachedRef.current = true;
    void (async () => {
      try {
        const res = await fetch("/api/agent/runs/active");
        if (!res.ok) return;
        const rows = (await res.json()) as { run_id: string; session_id: string }[];
        // 刷新后侧栏该显示「运行中」的都要显示：只接第一条会让其它会话看起来
        // 已经停了，用户点进去才发现任务其实还在跑。
        for (const row of rows) markLive(row.session_id, row.run_id);
        const first = rows[0];
        if (!first) return;
        setSessionId(first.session_id);
        void attach(first.run_id, first.session_id);
      } catch {
        // 探测失败不影响手动发起任务
      }
    })();
  }, [attach, markLive]);

  const submit = () => {
    const text = task;
    if (!text.trim() || running) return;
    setTask("");
    void run(text);
  };

  /**
   * 对敏感工具作出决定：批准后从检查点续跑，拒绝则让模型换路线。
   *
   * `scope="session"` 是「本次会话都允许」：除解开当前这次暂停外，还在服务端
   * 记一条持续授权，本对话里后续同类调用不再询问（有效期 8 小时）。重复点
   * 只刷新不叠加。注意它对宿主执行同样生效——用户明确表达过这个意图。
   */
  const approveTool = async (
    decision: "approve" | "reject" = "approve",
    scope: "once" | "session" = "once",
  ) => {
    if (!approval) return;
    const res = await fetch("/api/agent/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId ?? "",
        tool: approval.tool,
        args_hash: approval.argsHash,
        decision,
        scope,
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

  /**
   * 键盘批准：Ctrl/⌘ + Enter 直接放行当前这次。
   *
   * 确认条原本只能点按钮，鼠标要从正文挪到底部——那一步的摩擦比确认本身更
   * 让人烦。常见路径给快捷键，用户就不会为了少点一次去关掉整个审批。
   */
  useEffect(() => {
    if (!approval) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        void approveTool("approve", "once");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // approveTool 每次渲染都会重建，用 approval 作为重新订阅的时机即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval]);

  /** 选一个模板：把起步参数填进表单，执行仍由智能体自己规划。 */  const applyTemplate = async (tpl: TaskTemplate) => {
    if (running) return;
    setTask(tpl.prompt);
    setTemplateTools(tpl.tool_ids);
    setSelectedKbs(tpl.kb_ids);
    if (tpl.project) setBoundProject(tpl.project);
    // 计一次使用（失败不影响填表：模板的价值是省打字，不是统计）
    try {
      await fetch(`/api/templates/${tpl.id}/use`, { method: "POST" });
    } catch {
      /* 忽略 */
    }
    setTemplates((prev) =>
      prev.map((t) => (t.id === tpl.id ? { ...t, use_count: t.use_count + 1 } : t)),
    );
  };

  const stop = () => {
    const runId = runIdRef.current;
    runIdRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setElapsed(Date.now() - startedAtRef.current);
    // 用户明确要停：无论后端回什么都别再标「在跑」了
    clearLive(runId);
    // 断开 SSE 只是「我不看了」——执行在服务端独立 task 里，模型照跑、照烧 token。
    // 真正的停止必须显式通知后端撤销任务。
    if (!runId) return;
    void fetch(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" })
      .then(async (res) => {
        if (res.ok) {
          setSteps((prev) => [
            ...prev,
            { kind: "note", content: "已停止：执行已中断，已完成的步骤与产物保留在项目里。" },
          ]);
          return;
        }
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        setError(body.detail || `停止失败：HTTP ${res.status}`);
      })
      .catch(() => {
        setError("停止请求没有送达服务端，任务可能仍在后台运行——刷新页面可重新连上确认。");
      });
  };

  /**
   * 换一个视角：只断开发往浏览器的这条流，不动服务端任务。
   *
   * 以前这里直接 `if (running) return` —— 任务一开跑，整个侧栏就废了：点哪个
   * 历史任务都没反应，用户只能干等，或者去点「停止」（而那条停止请求对已经
   * 没有本地任务的运行还会 404）。执行本来就与「谁在看」解耦，界面没有理由
   * 跟着锁。
   */
  const detachView = () => {
    if (!running) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setElapsed(null);
  };

  const newTask = () => {
    detachView();
    runIdRef.current = null;
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
    setTemplateTools([]);
  };

  /** 打开一个任务会话：更早的轮次进历史，最后一轮作为当前轮展示 */
  const selectSession = (session: TaskSession) => {
    detachView();
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
    setApproval(null);
    setCitations([]);
    const pid = last.project || session.project;
    if (pid && pid !== boundProject) bindProject(pid);
    // 这个会话还有任务在后台跑 → 重新挂上，看它的实时进度
    const live = liveRunsRef.current[session.id];
    if (live) void attach(live, session.id);
  };

  const createProject = async () => {
    const name = await promptDialog({
      title: "新建项目",
      message: "项目名称支持字母、数字、中文与连字符",
      placeholder: "project-name",
      confirmLabel: "创建",
    });
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

  const deleteSession = async (session: TaskSession) => {
    const label = session.title.slice(0, 30) + (session.title.length > 30 ? "…" : "");
    const extra = session.runs.length > 1 ? `（含 ${session.runs.length} 轮对话）` : "";
    const ok = await confirmDialog({
      title: "删除任务",
      message: `删除任务「${label}」${extra}？此操作不可恢复。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
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
      {sidebarOpen && (
        <div
          className="absolute inset-0 z-20 bg-black/30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <div
        className={cn(
          "shrink-0 overflow-hidden transition-[width] duration-200 ease-out max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-[var(--shadow-pop)]",
          sidebarOpen ? "w-[264px]" : "w-0",
        )}
      >
        <TaskSidebar
          sessions={sessions}
          activeSessionId={sessionId}
          liveSessionIds={liveSessions}
          onSelectSession={selectSession}
          onDeleteSession={(session) => void deleteSession(session)}
          onNewTask={newTask}
          query={query}
          onQuery={setQuery}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col bg-surface-1">
        {/* 顶栏：标题 + 右侧操作 */}
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            title={sidebarOpen ? "收起任务栏" : "展开任务栏"}
            aria-label={sidebarOpen ? "收起任务栏" : "展开任务栏"}
            aria-expanded={sidebarOpen}
            className="rounded-md p-1.5 text-tertiary transition-colors duration-150 hover:bg-hover hover:text-primary"
          >
            <PanelLeft size={15} strokeWidth={1.9} />
          </button>

          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-primary">{title}</div>
            <div className="truncate text-[12px] text-tertiary">
              {boundProject ? `项目 · ${boundProject}` : "未绑定项目（由智能体按任务决定）"} · {caps.length} 项能力
              {selectedKbs.length > 0 && ` · ${selectedKbs.length} 个知识库`}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => setSaveOpen(true)}
              disabled={running || !(task.trim() || submitted)}
              title="把当前任务存成模板，下次一键复用"
              className="rounded-md px-2 py-1 text-[12px] text-secondary transition-colors duration-150 hover:bg-hover hover:text-primary disabled:opacity-40"
            >
              存为模板
            </button>
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
                <BrandMark size={44} className="mb-4 rounded-[13px] shadow-[0_14px_30px_-16px_var(--brand)]" />
                <h1 className="text-[21px] font-semibold text-primary">
                  今天想做点什么？
                </h1>
                <p className="mt-1.5 text-[13px] text-secondary">
                  把任务交给智能体：自己查资料、写文件、跑代码，直接产出可下载的成果
                </p>
              </div>

              <TemplateBar items={templates} disabled={running} onPick={(tpl) => void applyTemplate(tpl)} />

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
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
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

            {/* 输入区：与消息滚动区留同宽滚动条槽，保证正文列与输入器逐像素对齐 */}
            <div className="shrink-0 overflow-y-hidden pb-3 [scrollbar-gutter:stable]">
              <div className="conversation-column">
                {approval && (
                  <div className="mb-2 rounded-xl border border-warning/40 bg-warning/10 px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0 text-[13px] text-primary">
                        <p>
                          任务已暂停：工具{" "}
                          <code className="rounded bg-surface-3 px-1 py-0.5 text-xs">
                            {approval.tool}
                          </code>{" "}
                          {approval.env === "host"
                            ? "要在宿主进程里执行，能读到本机文件与服务端环境变量。"
                            : "需要你确认后才能执行。"}
                        </p>
                        {approval.args && (
                          <p
                            className="mt-0.5 truncate text-xs text-tertiary"
                            title={approval.args}
                          >
                            参数：{approval.args}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Button
                          onClick={() => void approveTool("approve", "once")}
                          title="Ctrl/⌘ + Enter"
                        >
                          批准并继续
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => void approveTool("approve", "session")}
                          title="本对话内不再询问该工具，8 小时内有效"
                        >
                          本次会话都允许
                        </Button>
                        <Button variant="ghost" onClick={() => void approveTool("reject")}>
                          拒绝
                        </Button>
                      </div>
                    </div>
                    <p className="mt-1.5 text-[11px] text-tertiary">
                      Ctrl/⌘ + Enter 直接批准；「本次会话都允许」只对当前对话有效，
                      换新对话会重新询问。
                    </p>
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

      <SaveTemplateDialog
        open={saveOpen}
        prompt={task.trim() || submitted}
        onClose={() => setSaveOpen(false)}
        onSaved={(tpl) => setTemplates((prev) => [tpl, ...prev])}
      />
    </div>
  );
}
