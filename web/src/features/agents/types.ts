/** 智能体页的共享类型与展示映射。 */

import {
  Calculator,
  Clock,
  Database,
  FileText,
  FolderPlus,
  Globe,
  Presentation,
  Sparkles,
  Terminal,
  Type,
  type LucideIcon,
} from "lucide-react";

export interface ToolInfo {
  id: string;
  name: string;
  description: string;
  group: string;
  tools?: string[];
  default?: boolean;
}

export interface PlanItem {
  content: string;
  status: "pending" | "in_progress" | "completed" | string;
}

export interface AgentStep {
  kind: "tool_call" | "tool_result" | "plan" | "approval_required" | "note";
  /** plan 帧没有工具名，所以可选 */
  name?: string;
  /** approval_required 帧用 tool 携带待批准的工具 id */
  tool?: string;
  args?: string;
  content?: string;
  duration_ms?: number | null;
  /** kind=plan 时的计划清单（deepagents 的 write_todos） */
  items?: PlanItem[];
}

export interface Artifact {
  path: string;
  kind?: string;
}

export interface KbInfo {
  id: string;
  name: string;
  document_count: number;
}

/** 技能（智能体可挂载；选中 = 强制装载，不选 = 引擎按任务自动挑选） */
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
}

export interface ProjectInfo {
  id: string;
  name: string;
  files: number;
  size: string;
  updated_at: string;
}

export interface TreeNode {
  path: string;
  type: "file" | "dir";
  size?: string;
}

export interface AgentRunItem {
  id: string;
  task: string;
  tool_ids: string[];
  steps: AgentStep[];
  answer: string;
  status: string;
  created_at: string;
  project?: string;
  artifacts?: Artifact[];
  /** 同一任务的追问共享同一个 session_id */
  session_id?: string;
  turn?: number;
}

/** 侧栏里的「任务」= 一个会话（可能包含多轮追问） */
export interface TaskSession {
  id: string;
  /** 首轮提问，作为任务标题 */
  title: string;
  /** 按时间正序排列的所有轮次 */
  runs: AgentRunItem[];
  created_at: string;
  updated_at: string;
  project?: string;
  steps: number;
  artifacts: number;
}

/** 把扁平的 run 列表按会话聚合，最近活跃的排在前面 */
export function groupSessions(runs: AgentRunItem[]): TaskSession[] {
  const map = new Map<string, AgentRunItem[]>();
  for (const run of runs) {
    const key = run.session_id || run.id;
    map.set(key, [...(map.get(key) ?? []), run]);
  }
  const sessions: TaskSession[] = [];
  for (const [id, list] of map) {
    const ordered = [...list].sort((a, b) => a.created_at.localeCompare(b.created_at));
    const first = ordered[0];
    const last = ordered[ordered.length - 1];
    if (!first || !last) continue;
    sessions.push({
      id,
      title: first.task,
      runs: ordered,
      created_at: first.created_at,
      updated_at: last.created_at,
      project: [...ordered].reverse().find((r) => r.project)?.project,
      steps: ordered.reduce((n, r) => n + r.steps.filter((s) => s.kind === "tool_call").length, 0),
      artifacts: ordered.reduce((n, r) => n + (r.artifacts?.length ?? 0), 0),
    });
  }
  return sessions.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export const TOOL_LABELS: Record<string, string> = {
  web_search: "网页搜索",
  web_fetch: "抓取正文",
  project_create: "创建项目",
  fs_write: "写文件",
  fs_read: "读文件",
  fs_list: "查看文件",
  code_run: "运行代码",
  ppt_create: "生成 PPT",
  calculator: "计算",
  current_time: "读取时间",
  text_stats: "文本统计",
  kb_search: "知识库检索",
  // deepagents harness 自带的文件/计划工具
  write_file: "写文件",
  read_file: "读文件",
  edit_file: "改文件",
  ls: "列目录",
  glob: "找文件",
  grep: "搜内容",
  delete: "删文件",
  task: "子任务",
  write_todos: "更新计划",
};

export const TOOL_ICONS: Record<string, LucideIcon> = {
  web_search: Globe,
  web_fetch: Globe,
  project_create: FolderPlus,
  fs_write: FileText,
  fs_read: FileText,
  fs_list: FileText,
  code_run: Terminal,
  ppt_create: Presentation,
  calculator: Calculator,
  current_time: Clock,
  text_stats: Type,
  kb_search: Database,
  write_file: FileText,
  edit_file: FileText,
  read_file: FileText,
  ls: FolderPlus,
  glob: FolderPlus,
  grep: Type,
  delete: FileText,
  task: Sparkles,
  write_todos: Sparkles,
};

export const toolLabel = (id: string) => TOOL_LABELS[id] ?? id;
export const toolIcon = (id: string) => TOOL_ICONS[id] ?? FileText;

export function fileName(path: string) {
  return path.split("/").pop() ?? path;
}

export function downloadUrl(project: string, path: string) {
  return `/api/projects/${encodeURIComponent(project)}/download?path=${encodeURIComponent(path)}`;
}

/** 产物类型 → 卡片上的副标题 */
export function artifactKindLabel(artifact: Artifact): string {
  const path = artifact.path.toLowerCase();
  if (path.endsWith(".pptx")) return "演示文稿 · 可编辑";
  if (path.endsWith(".html")) return "网页 · 可预览";
  if (path.endsWith(".md")) return "文档 · Markdown";
  if (path.endsWith(".csv") || path.endsWith(".xlsx")) return "数据表";
  if (/\.(png|jpg|jpeg|webp|gif|svg)$/.test(path)) return "图片";
  if (path.endsWith(".pdf")) return "PDF";
  if (artifact.kind === "web") return "网页";
  return "文件";
}

/** 相对时间，侧栏任务列表用。 */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diff = Date.now() - then;
  const minute = 60_000;
  if (diff < minute) return "刚刚";
  if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < 24 * 60 * minute) return `${Math.floor(diff / (60 * minute))} 小时前`;
  const d = new Date(then);
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

/** 侧栏分组：今天 / 昨天 / 更早 */
export function dayBucket(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startOfToday) return "今天";
  if (t >= startOfToday - 86_400_000) return "昨天";
  if (t >= startOfToday - 7 * 86_400_000) return "近 7 天";
  return "更早";
}
