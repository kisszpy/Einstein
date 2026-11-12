/**
 * AI PPT 制作的后端访问层。JSON 请求走全局 fetch（拦截器注入鉴权与工作区）；
 * 模板上传用 FormData（不能预设 Content-Type，否则 boundary 会被覆盖）。
 */

import type {
  DesignCatalog,
  Outline,
  PptGeneration,
  PptMessage,
  PptSession,
  PreviewResult,
  SourceRef,
  ThemePackDetail,
  ThemePackSummary,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData 必须让浏览器自己带 boundary，预设 application/json 会让后端解析不出文件
  const jsonHeader: Record<string, string> =
    init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(path, {
    ...init,
    headers: { ...jsonHeader, ...(init?.headers ?? {}) },
  });
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
  if (!res.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : `请求失败（${res.status}）`;
    throw new Error(message);
  }
  return data as T;
}

// ---------------------------------------------------------------- 主题包

export function listThemePacks(): Promise<ThemePackSummary[]> {
  return request<ThemePackSummary[]>("/api/ppt/templates");
}

export function getThemePack(id: string): Promise<ThemePackDetail> {
  return request<ThemePackDetail>(`/api/ppt/templates/${encodeURIComponent(id)}`);
}

export function uploadThemePack(file: File, name = ""): Promise<ThemePackSummary> {
  const form = new FormData();
  form.append("file", file);
  if (name) form.append("name", name);
  return request<ThemePackSummary>("/api/ppt/templates", { method: "POST", body: form });
}

export function deleteThemePack(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/ppt/templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------- 会话

export interface CreateSessionInput {
  title?: string;
  project?: string;
  expert_id?: string;
  skill_ids?: string[];
  kb_ids?: string[];
  theme_pack_id?: string;
}

export function listSessions(limit = 50): Promise<PptSession[]> {
  return request<PptSession[]>(`/api/ppt/sessions?limit=${limit}`);
}

export function createSession(input: CreateSessionInput): Promise<PptSession> {
  return request<PptSession>("/api/ppt/sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getSession(
  id: string,
): Promise<{ session: PptSession; messages: PptMessage[] }> {
  return request<{ session: PptSession; messages: PptMessage[] }>(
    `/api/ppt/sessions/${encodeURIComponent(id)}`,
  );
}

export function deleteSession(id: string): Promise<{ ok: boolean; deleted_messages: number }> {
  return request<{ ok: boolean; deleted_messages: number }>(
    `/api/ppt/sessions/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export interface SaveOutlineResult {
  outline: Outline;
  page_count: number;
  total_slides: number;
  warnings: string[];
  message_id: string;
}

export function saveOutline(
  sessionId: string,
  outline: Outline,
  messageId?: string,
): Promise<SaveOutlineResult> {
  return request<SaveOutlineResult>(
    `/api/ppt/sessions/${encodeURIComponent(sessionId)}/outline`,
    { method: "PUT", body: JSON.stringify({ outline, message_id: messageId }) },
  );
}

export function listGenerations(sessionId: string): Promise<PptGeneration[]> {
  return request<PptGeneration[]>(
    `/api/ppt/sessions/${encodeURIComponent(sessionId)}/generations`,
  );
}

export interface AnalyzeInput {
  prompt: string;
  expert_id?: string;
  skill_ids?: string[];
  kb_ids?: string[];
  theme_pack_id?: string;
  page_target?: number;
}

export function analyzeRequest(sessionId: string, input: AnalyzeInput, signal?: AbortSignal): Promise<Response> {
  return fetch(`/api/ppt/sessions/${encodeURIComponent(sessionId)}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}

export interface GenerateInput {
  outline?: Outline;
  project?: string;
  filename?: string;
}

export function generateRequest(
  sessionId: string,
  input: GenerateInput,
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`/api/ppt/sessions/${encodeURIComponent(sessionId)}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}

export type { SourceRef };

// ---------------------------------------------------------------- 设计台

export function getDesigns(): Promise<DesignCatalog> {
  return request<DesignCatalog>("/api/ppt/designs");
}

/** 实时预览：只读，不落盘也不签票。浏览器不可用时后端返回 available=false。 */
export function previewDesign(
  sessionId: string,
  outline: Outline,
  limit = 6,
  signal?: AbortSignal,
): Promise<PreviewResult> {
  return request<PreviewResult>(
    `/api/ppt/sessions/${encodeURIComponent(sessionId)}/preview`,
    { method: "POST", body: JSON.stringify({ outline, limit }), signal },
  );
}

/**
 * 结果卡缩略图地址。<img> 带不了 Authorization，所以走短期票据；
 * 票据由 listGenerations 每次重签，deck 为空（项目已删）时没有可用票据。
 */
export function thumbUrl(generation: PptGeneration, index: number): string {
  const ticket = generation.deck?.ticket ?? "";
  const base = `/api/ppt/thumbs/${encodeURIComponent(generation.generation_id)}/${index}`;
  return ticket ? `${base}?ticket=${encodeURIComponent(ticket)}` : base;
}
