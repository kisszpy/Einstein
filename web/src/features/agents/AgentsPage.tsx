import { useCallback, useEffect, useRef, useState } from "react";
import { readSseStream } from "@/features/chat/sse-parser";
import { Markdown } from "@/features/chat/components/Markdown";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  ChevronDown,
  Download,
  FileText,
  FolderPlus,
  Globe,
  MonitorPlay,
  Presentation,
  RefreshCw,
  Terminal,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface ToolInfo {
  id: string;
  name: string;
  description: string;
  group: string;
  tools?: string[];
  default?: boolean;
}

interface AgentStep {
  kind: "tool_call" | "tool_result";
  name: string;
  args?: string;
  content?: string;
  duration_ms?: number | null;
}

interface Artifact {
  path: string;
  kind?: string;
}

interface KbInfo {
  id: string;
  name: string;
  document_count: number;
}

interface ProjectInfo {
  id: string;
  name: string;
  files: number;
  size: string;
  updated_at: string;
}

interface TreeNode {
  path: string;
  type: "file" | "dir";
  size?: string;
}

interface AgentRunItem {
  id: string;
  task: string;
  tool_ids: string[];
  steps: AgentStep[];
  answer: string;
  status: string;
  created_at: string;
  project?: string;
  artifacts?: Artifact[];
}

const TOOL_LABELS: Record<string, string> = {
  web_search: "网页搜索",
  web_fetch: "抓取正文",
  project_create: "创建项目",
  fs_write: "写文件",
  fs_read: "读文件",
  fs_list: "文件树",
  code_run: "运行代码",
  ppt_create: "生成 PPT",
  calculator: "计算器",
  current_time: "当前时间",
  text_stats: "文本统计",
  kb_search: "知识库检索",
};

const TOOL_ICONS: Record<string, typeof Globe> = {
  web_search: Globe,
  web_fetch: Globe,
  project_create: FolderPlus,
  fs_write: FileText,
  fs_read: FileText,
  fs_list: FileText,
  code_run: Terminal,
  ppt_create: Presentation,
};

const toolLabel = (id: string) => TOOL_LABELS[id] ?? id;

const CAP_ICONS: Record<string, typeof Globe> = {
  web: Globe,
  files: FolderPlus,
  code: Terminal,
  ppt: Presentation,
};

function fileName(path: string) {
  return path.split("/").pop() ?? path;
}

function downloadUrl(project: string, path: string) {
  return `/api/projects/${encodeURIComponent(project)}/download?path=${encodeURIComponent(path)}`;
}

export function AgentsPage() {
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
  const [treeOpen, setTreeOpen] = useState(false);
  const [task, setTask] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [answer, setAnswer] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgentRunItem[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadProjects = useCallback(async () => {
    const res = await fetch("/api/projects");
    setProjects(await res.json());
  }, []);

  const loadRuns = useCallback(async () => {
    const res = await fetch("/api/agent/runs");
    setRuns(await res.json());
  }, []);

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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [steps, answer, artifacts]);

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

  const selectProject = (pid: string) => {
    setProject(pid);
    setTreeOpen(!!pid);
    void loadTree(pid);
  };

  const toggleCap = (id: string) => {
    setCaps((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));
  };

  const toggleKb = (id: string) => {
    setSelectedKbs((prev) => (prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id]));
  };

  const capabilityTools = caps.flatMap((capId) => {
    const cap = toolMeta.capabilities.find((c) => c.id === capId);
    return cap?.tools ?? [];
  });

  const run = useCallback(
    async (taskText: string) => {
      if (!taskText.trim() || running) return;
      setSteps([]);
      setArtifacts([]);
      setAnswer("");
      setError(null);
      setRunning(true);
      try {
        const res = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task: taskText.trim(),
            tool_ids: capabilityTools,
            kb_ids: selectedKbs,
            project: project,
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
              break;
            }
            case "done": {
              const d = raw as { project?: string; artifacts?: Artifact[] };
              if (d.project && d.project !== project) selectProject(d.project);
              else if (d.project) void loadTree(d.project);
              void loadProjects();
              break;
            }
            case "delta": {
              const { text } = raw as { text: string };
              setAnswer((prev) => prev + text);
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
        setRunning(false);
        void loadRuns();
      }
    },
    [capabilityTools, selectedKbs, project, running, loadRuns, loadProjects, loadTree],
  );

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
      selectProject(info.project);
    }
  };

  const deleteProject = async (pid: string) => {
    if (!window.confirm(`删除项目「${pid}」及其全部文件？此操作不可恢复。`)) return;
    await fetch(`/api/projects/${encodeURIComponent(pid)}`, { method: "DELETE" });
    if (project === pid) selectProject("");
    void loadProjects();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1100px] space-y-6 px-8 py-8">
        <div>
          <h1 className="text-lg font-medium">智能体工作台</h1>
          <p className="mt-1 text-[13px] text-secondary">
            工作型智能体：自主联网查资料、创建项目写文件、运行代码验证、直接产出 PPT。过程与产物全程可见。
          </p>
        </div>

        <div className="grid grid-cols-[1fr_320px] items-start gap-6">
          {/* 左：能力 + 任务 + 过程 */}
          <div className="space-y-6">
            <Card className="p-5">
              <h3 className="text-[13px] font-medium">能力</h3>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {toolMeta.capabilities.map((cap) => {
                  const Icon = CAP_ICONS[cap.id] ?? Globe;
                  const on = caps.includes(cap.id);
                  return (
                    <button
                      key={cap.id}
                      onClick={() => toggleCap(cap.id)}
                      className={cn(
                        "flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors duration-150",
                        on ? "border-brand bg-brand-soft" : "border-line hover:border-line-strong",
                      )}
                    >
                      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", on ? "text-brand" : "text-tertiary")} strokeWidth={1.75} />
                      <div>
                        <div className={cn("text-[13px]", on ? "text-brand" : "text-primary")}>{cap.name}</div>
                        <div className="text-xs text-tertiary">{cap.description}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
              {kbs.length > 0 && (
                <div className="mt-4 border-t border-line pt-4">
                  <h3 className="text-[13px] font-medium">
                    知识库<span className="ml-2 font-normal text-tertiary">选中后自动启用检索工具</span>
                  </h3>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {kbs.map((k) => (
                      <button
                        key={k.id}
                        onClick={() => toggleKb(k.id)}
                        className={cn(
                          "rounded-lg border px-3 py-1.5 text-[13px] transition-colors duration-150",
                          selectedKbs.includes(k.id)
                            ? "border-brand bg-brand-soft text-brand"
                            : "border-line text-primary hover:border-line-strong",
                        )}
                      >
                        {k.name}
                        <span className="ml-1.5 text-xs text-tertiary">{k.document_count}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </Card>

            <Card className="flex items-end gap-2 p-3">
              <textarea
                value={task}
                onChange={(e) => setTask(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void run(task);
                  }
                }}
                placeholder="给智能体一个任务，例如：查一下 2025 年大模型市场的预测数据，写份调研并做成 PPT"
                rows={2}
                className="max-h-40 flex-1 resize-none bg-transparent px-2 text-sm outline-none placeholder:text-tertiary"
              />
              <Button disabled={running || !task.trim()} onClick={() => void run(task)}>
                {running ? "运行中…" : "运行"}
              </Button>
            </Card>

            {(steps.length > 0 || answer || error || artifacts.length > 0) && (
              <Card className="space-y-2 p-5">
                <h3 className="text-[13px] font-medium">运行过程</h3>
                {steps.map((s, i) => {
                  const Icon = TOOL_ICONS[s.name] ?? FileText;
                  const isCall = s.kind === "tool_call";
                  const detail = isCall ? s.args : s.content;
                  const open = expanded[i];
                  return (
                    <div
                      key={i}
                      className={cn("rounded-lg px-3 py-2", isCall ? "bg-brand-soft" : "bg-surface-3")}
                    >
                      <button
                        className="flex w-full items-center gap-2 text-left"
                        onClick={() => setExpanded((prev) => ({ ...prev, [i]: !prev[i] }))}
                      >
                        <Icon
                          className={cn("h-3.5 w-3.5 shrink-0", isCall ? "text-brand" : "text-success")}
                          strokeWidth={1.75}
                        />
                        <span className={cn("shrink-0 text-[13px]", isCall ? "text-brand" : "text-success")}>
                          {isCall ? toolLabel(s.name) : `完成 · ${toolLabel(s.name)}`}
                        </span>
                        {!isCall && s.duration_ms != null && (
                          <span className="shrink-0 text-xs text-tertiary">{s.duration_ms}ms</span>
                        )}
                        <span className="min-w-0 flex-1 truncate text-xs text-secondary">
                          {isCall ? detail : (detail ?? "").replace(/\s+/g, " ").slice(0, 120)}
                        </span>
                        <ChevronDown
                          className={cn("h-3.5 w-3.5 shrink-0 text-tertiary transition-transform", open && "rotate-180")}
                          strokeWidth={1.75}
                        />
                      </button>
                      {open && (
                        <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-2 p-2.5 text-xs leading-relaxed text-secondary">
                          {detail}
                        </pre>
                      )}
                    </div>
                  );
                })}

                {artifacts.length > 0 && (
                  <div className="mt-3 rounded-lg border border-line p-3">
                    <p className="mb-2 text-xs text-tertiary">产出物</p>
                    <div className="flex flex-wrap gap-2">
                      {artifacts.map((a) => (
                        <a
                          key={a.path}
                          href={project ? downloadUrl(project, a.path) : undefined}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[13px] text-primary transition-colors duration-150 hover:border-brand hover:text-brand"
                        >
                          <Presentation className="h-3.5 w-3.5 text-brand" strokeWidth={1.75} />
                          {fileName(a.path)}
                          <Download className="h-3 w-3 text-tertiary" strokeWidth={1.75} />
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                {answer && (
                  <div className="mt-3 border-t border-line pt-3">
                    <p className="mb-1 text-xs text-tertiary">最终回答</p>
                    <Markdown content={answer} />
                  </div>
                )}
                {error && (
                  <p className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>
                )}
                <div ref={bottomRef} />
              </Card>
            )}
          </div>

          {/* 右：项目面板 */}
          <div className="space-y-4">
            <Card className="p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-medium">项目</h3>
                <button
                  onClick={() => void createProject()}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-secondary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
                >
                  <FolderPlus className="h-3.5 w-3.5" strokeWidth={1.75} />
                  新建
                </button>
              </div>
              <select
                value={project}
                onChange={(e) => selectProject(e.target.value)}
                className="mt-2.5 h-9 w-full rounded-lg border border-line bg-surface-2 px-2 text-[13px] outline-none focus:border-line-strong"
              >
                <option value="">运行时由智能体创建</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}（{p.files} 文件 · {p.size}）
                  </option>
                ))}
              </select>

              {project && treeOpen && (
                <div className="mt-3 border-t border-line pt-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-tertiary">文件树</p>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => void loadTree(project)}
                        title="刷新"
                        className="rounded p-1 text-tertiary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
                      >
                        <RefreshCw className="h-3 w-3" strokeWidth={1.75} />
                      </button>
                      <a
                        href={`/api/projects/${encodeURIComponent(project)}/archive`}
                        title="下载整个项目（zip）"
                        className="rounded p-1 text-tertiary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
                      >
                        <Download className="h-3 w-3" strokeWidth={1.75} />
                      </a>
                      <button
                        onClick={() => void deleteProject(project)}
                        title="删除项目"
                        className="rounded p-1 text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger"
                      >
                        <Trash2 className="h-3 w-3" strokeWidth={1.75} />
                      </button>
                    </div>
                  </div>
                  <div className="mt-1.5 max-h-72 space-y-0.5 overflow-y-auto">
                    {tree.length === 0 && <p className="px-1 py-2 text-xs text-tertiary">（空项目）</p>}
                    {tree.map((node) => {
                      const depth = node.path.split("/").length - 1;
                      if (node.type === "dir") {
                        return (
                          <div
                            key={node.path}
                            className="truncate rounded px-1.5 py-1 text-xs text-secondary"
                            style={{ paddingLeft: `${8 + depth * 14}px` }}
                          >
                            📁 {node.path.split("/").pop()}
                          </div>
                        );
                      }
                      return (
                        <a
                          key={node.path}
                          href={downloadUrl(project, node.path)}
                          className="flex items-center justify-between gap-2 truncate rounded px-1.5 py-1 text-xs text-primary transition-colors duration-150 hover:bg-surface-3 hover:text-brand"
                          style={{ paddingLeft: `${8 + depth * 14}px` }}
                          title={`下载 ${node.path}`}
                        >
                          <span className="truncate">{node.path.split("/").pop()}</span>
                          <span className="shrink-0 text-tertiary">{node.size}</span>
                        </a>
                      );
                    })}
                  </div>
                </div>
              )}
            </Card>

            {runs.length > 0 && (
              <Card className="p-4">
                <h3 className="text-[13px] font-medium">运行历史（{runs.length}）</h3>
                <div className="mt-2.5 max-h-96 space-y-1 overflow-y-auto">
                  {runs.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => {
                        setSteps(r.steps);
                        setAnswer(r.answer);
                        setArtifacts(r.artifacts ?? []);
                        setError(null);
                        setTask(r.task);
                        if (r.project) selectProject(r.project);
                      }}
                      className="block w-full rounded-lg px-2.5 py-2 text-left transition-colors duration-150 hover:bg-surface-3"
                    >
                      <div className="truncate text-[13px] text-primary">{r.task}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-xs text-tertiary">
                        <MonitorPlay className="h-3 w-3" strokeWidth={1.75} />
                        {new Date(r.created_at).toLocaleString("zh-CN")} · {r.steps.filter((s) => s.kind === "tool_call").length} 次工具
                        {r.project && ` · ${r.project}`}
                        {r.artifacts && r.artifacts.length > 0 && ` · ${r.artifacts.length} 产物`}
                      </div>
                    </button>
                  ))}
                </div>
              </Card>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
