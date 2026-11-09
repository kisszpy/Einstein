/** KB 页面共享的类型与小工具（来源同步 / 授权）。 */

export interface Grant {
  id: string;
  kb_id: string;
  user_id: string;
  role: "read" | "write";
  granted_by: string;
  created_at: string;
}

export interface GrantCandidate {
  user_id: string;
  name: string;
  username: string;
  workspace_role: string;
  granted: "" | "read" | "write";
}

export interface RefreshItemResult {
  id: string;
  name: string;
  changed: boolean;
  ok: boolean;
  error: string;
  version: number;
  chunk_count: number;
}

export interface BatchRefreshResult {
  total: number;
  changed: number;
  failed: number;
  items: RefreshItemResult[];
}

export interface StaleResult {
  threshold_days: number;
  count: number;
  items: {
    id: string;
    name: string;
    version: number;
    source_url: string;
    fetched_at: string;
  }[];
}

/** 从失败响应里取出可读原因；后端详情是纯文本时给出兜底文案。 */
export async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* 非 JSON 响应 */
  }
  return `请求失败（HTTP ${res.status}）`;
}

/** ISO 时间 → 简洁中文相对时间；超过 30 天退回日期。 */
export function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diff = Date.now() - t;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "刚刚";
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  const days = Math.floor(diff / day);
  if (days === 1) return "昨天";
  if (days <= 30) return `${days} 天前`;
  return iso.replace("T", " ").slice(0, 10);
}

/** 取 URL 域名；解析失败时原样返回。 */
export function sourceHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
