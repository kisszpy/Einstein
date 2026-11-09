/** 内置浏览器的后端访问层。路径都走 vite 代理，鉴权与工作区由全局 fetch 拦截器注入。 */

import type { Deck, ProjectInfo, ReadPayload, ThemePreset, TreeEntry } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { detail?: string }).detail || `请求失败（${res.status}）`);
  }
  return data as T;
}

export function listProjects(): Promise<ProjectInfo[]> {
  return request<ProjectInfo[]>("/api/projects");
}

export function projectTree(project: string): Promise<{ entries: TreeEntry[] }> {
  return request<{ entries: TreeEntry[] }>(`/api/projects/${encodeURIComponent(project)}/tree`);
}

export function readFile(project: string, path: string) {
  // 后端返回 { path, binary, bytes, truncated, text }，这里归一成 content 供编辑器使用
  return request<{ path: string; binary: boolean; bytes: number; truncated: boolean; text: string }>(
    `/api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(path)}`,
  ).then((data) => ({
    path: data.path,
    content: data.text ?? "",
    bytes: data.bytes as number | undefined,
    truncated: Boolean(data.truncated),
  }));
}

export function writeFile(project: string, path: string, content: string) {
  return request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(project)}/file`, {
    method: "PUT",
    body: JSON.stringify({ path, content }),
  });
}

export function readDeck(project: string, path: string): Promise<Deck> {
  return request<Deck>(
    `/api/projects/${encodeURIComponent(project)}/deck?path=${encodeURIComponent(path)}`,
  );
}

export function saveDeck(project: string, path: string, deck: Deck): Promise<Deck> {
  const { path: _p, bytes: _b, updated_at: _u, source: _s, editable: _e, ...rest } = deck;
  void _p;
  void _b;
  void _u;
  void _s;
  void _e;
  return request<Deck>(
    `/api/projects/${encodeURIComponent(project)}/deck?path=${encodeURIComponent(path)}`,
    { method: "PUT", body: JSON.stringify({ deck: rest }) },
  );
}

export function listThemes(): Promise<ThemePreset[]> {
  return request<ThemePreset[]>("/api/browse/themes");
}

/** 票据给 iframe / img 用：这类请求带不了 Authorization 头，只能靠查询参数。 */
let cached: { ticket: string; expires: number } | null = null;

export async function getTicket(force = false): Promise<string> {
  if (!force && cached && cached.expires > Date.now()) return cached.ticket;
  const data = await request<{ ticket: string; expires_in: number }>("/api/browse/ticket");
  // 提前 60 秒续期，避免边界上刚好过期
  cached = { ticket: data.ticket, expires: Date.now() + (data.expires_in - 60) * 1000 };
  return data.ticket;
}

export function invalidateTicket(): void {
  cached = null;
}

export function previewUrl(project: string, path: string, ticket: string): string {
  return `/api/projects/${encodeURIComponent(project)}/preview?path=${encodeURIComponent(
    path,
  )}&ticket=${encodeURIComponent(ticket)}`;
}

export function downloadUrl(project: string, path: string): string {
  return `/api/projects/${encodeURIComponent(project)}/download?path=${encodeURIComponent(path)}`;
}

export function proxyUrl(url: string, ticket: string): string {
  return `/api/browse/web?url=${encodeURIComponent(url)}&ticket=${encodeURIComponent(ticket)}`;
}

export function readWebPage(url: string): Promise<ReadPayload> {
  return request<ReadPayload>(`/api/browse/read?url=${encodeURIComponent(url)}`);
}
