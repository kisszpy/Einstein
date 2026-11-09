/** 运营页用到的接口调用：错误统一转成带 detail 的 Error，页面直接展示。 */

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(data?.detail || `请求失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiSend<T>(path: string, method: "POST" | "PATCH", body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiDelete(path: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(path, { method: "DELETE" });
}
