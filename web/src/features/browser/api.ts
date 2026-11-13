/** 资产页（浏览器工作台）的后端访问层。路径都走 vite 代理，鉴权与工作区由全局 fetch 拦截器注入。 */

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

/** 删除整个项目（含目录内全部文件）。后端已有端点，这里补前端调用。 */
export function deleteProject(project: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(project)}`, {
    method: "DELETE",
  });
}

export function projectTree(project: string): Promise<{ entries: TreeEntry[] }> {
  return request<{ entries: TreeEntry[] }>(`/api/projects/${encodeURIComponent(project)}/tree`);
}

/** 浏览页用：只拉产物清单（后端按白名单后缀过滤），不再拉全量文件树。
 *  项目里混进依赖/解包残留时（如整套 Linux sysroot），产物不会被淹没。 */
export function projectArtifacts(
  project: string,
): Promise<{ entries: TreeEntry[]; count: number; truncated?: boolean }> {
  return request<{ entries: TreeEntry[]; count: number; truncated?: boolean }>(
    `/api/projects/${encodeURIComponent(project)}/artifacts`,
  );
}

/** 删除项目里的一个文件（只删文件，目录要另外的动作）。 */
export function deleteFile(
  project: string,
  path: string,
): Promise<{ path: string; bytes: number; total_files: number }> {
  return request<{ path: string; bytes: number; total_files: number }>(
    `/api/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
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

/** ArrayBuffer → base64。分块拼字符串：一次性 spread 几 MB 的 Uint8Array 会爆调用栈。 */
function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/** 写回二进制成品（放映级编辑器保存回传的 pptx）。走 /file-bytes，不是文本 /file。 */
export function saveFileBytes(
  project: string,
  path: string,
  bytes: ArrayBuffer,
): Promise<{ path: string; bytes: number; previous_bytes: number; created: boolean }> {
  return request(`/api/projects/${encodeURIComponent(project)}/file-bytes`, {
    method: "PUT",
    body: JSON.stringify({ path, content_base64: toBase64(bytes) }),
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

export interface DslDiagnostic {
  ruleId?: string;
  severity?: string;
  message?: string;
  [key: string]: unknown;
}

/** slidep 产物的 DSL 编辑链路：apply=false 只校验，true 通过后重编译替换该页。 */
export function applyDeckDsl(
  project: string,
  path: string,
  pageIndex: number,
  dsl: string,
  apply: boolean,
): Promise<{ ok: boolean; diagnostics: DslDiagnostic[]; deck?: Deck }> {
  return request<{ ok: boolean; diagnostics: DslDiagnostic[]; deck?: Deck }>(
    `/api/projects/${encodeURIComponent(project)}/deck/dsl?path=${encodeURIComponent(path)}`,
    { method: "PUT", body: JSON.stringify({ page_index: pageIndex, dsl, apply }) },
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

export function downloadUrl(project: string, path: string, ticket = ""): string {
  // `<a href>` 带不了 Authorization 头，下载和预览一样走短期票据鉴权，
  // 否则浏览器原生导航拿到的就是 401「需要登录」。
  const ticketQs = ticket ? `&ticket=${encodeURIComponent(ticket)}` : "";
  return `/api/projects/${encodeURIComponent(project)}/download?path=${encodeURIComponent(path)}${ticketQs}`;
}

export function proxyUrl(url: string, ticket: string): string {
  return `/api/browse/web?url=${encodeURIComponent(url)}&ticket=${encodeURIComponent(ticket)}`;
}

export function readWebPage(url: string): Promise<ReadPayload> {
  return request<ReadPayload>(`/api/browse/read?url=${encodeURIComponent(url)}`);
}
