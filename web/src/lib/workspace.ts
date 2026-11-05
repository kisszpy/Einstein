/**
 * 工作区上下文（多租户切换）。
 *
 * 控制台内的所有请求都靠 `X-Workspace` 头声明当前工作区；为了不改动各页面
 * 里已有的 fetch 调用，这里在入口处对 fetch 做一次薄拦截，统一注入请求头。
 */

const STORAGE_KEY = "ai-platform:workspace";
const DEFAULT_ID = "default";

let current: string =
  typeof localStorage === "undefined" ? DEFAULT_ID : localStorage.getItem(STORAGE_KEY) || DEFAULT_ID;

const listeners = new Set<(id: string) => void>();

export function getWorkspace(): string {
  return current;
}

export function setWorkspace(id: string): void {
  current = id;
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* 隐私模式下写入失败不影响内存态 */
  }
  listeners.forEach((fn) => fn(id));
}

export function subscribeWorkspace(fn: (id: string) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

let installed = false;

/** 对 /api/ 请求注入 X-Workspace（外部 URL 与 /v1 不受影响）。 */
export function installWorkspaceHeader(): void {
  if (installed) return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (!url.startsWith("/api/")) return original(input, init);
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set("X-Workspace", current);
    return original(input, { ...init, headers });
  };
}
