/**
 * 控制台登录态。
 *
 * 令牌存 localStorage，所有 `/api/` 请求由 fetch 拦截器自动带上
 * `Authorization: Bearer`；任何请求收到 401（登录接口除外）都视为会话失效，
 * 立刻清态并跳回登录页，避免页面停留在半残状态。
 */

import { useSyncExternalStore } from "react";

export interface AuthUser {
  id: string;
  username: string;
  display_name: string;
  role: "owner" | "admin" | "member" | string;
  role_label: string;
  workspaces: string[];
  created_at: string;
  last_login_at: string | null;
  disabled: boolean;
}

type Status = "idle" | "loading" | "authed" | "anon";

export interface AuthState {
  status: Status;
  user: AuthUser | null;
  workspaces: string[];
}

const TOKEN_KEY = "ai-platform:token";
const LAST_USER_KEY = "ai-platform:username";

let token: string | null = read(TOKEN_KEY);
let state: AuthState = { status: token ? "loading" : "anon", user: null, workspaces: [] };
let booting: Promise<void> | null = null;

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* 隐私模式下不影响内存态 */
  }
}

const listeners = new Set<() => void>();

function emit(next: AuthState) {
  state = next;
  listeners.forEach((fn) => fn());
}

export function getToken(): string | null {
  return token;
}

export function getAuthState(): AuthState {
  return state;
}

export function subscribeAuth(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState, getAuthState);
}

export function lastUsername(): string {
  return read(LAST_USER_KEY) ?? "";
}

export function clearSession(): void {
  token = null;
  write(TOKEN_KEY, null);
  emit({ status: "anon", user: null, workspaces: [] });
}

/** 登录：成功后写入令牌并返回用户信息。失败抛出可展示的错误文案。 */
export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `登录失败（${res.status}）`);

  token = data.token as string;
  write(TOKEN_KEY, token);
  write(LAST_USER_KEY, data.user.username);
  emit({ status: "authed", user: data.user, workspaces: data.workspaces ?? [] });
  return data.user as AuthUser;
}

export interface EmailCodeResult {
  delivery: "smtp" | "console";
  ttl: number;
  resend_after: number;
  /** 仅本机联调且未配置 SMTP 时返回；生产经网关访问拿不到 */
  code?: string;
}

/** 请求邮箱验证码。不区分邮箱是否已注册，避免被用来探测账号。 */
export async function requestEmailCode(email: string): Promise<EmailCodeResult> {
  const res = await fetch("/api/auth/email/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `发送失败（${res.status}）`);
  return data as EmailCodeResult;
}

/** 验证码校验；邮箱首次登录即注册。成功后的落态与密码登录完全一致。 */
export async function loginWithEmail(
  email: string,
  code: string,
  name = "",
): Promise<AuthUser> {
  const res = await fetch("/api/auth/email/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code, name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `验证失败（${res.status}）`);

  token = data.token as string;
  write(TOKEN_KEY, token);
  write(LAST_USER_KEY, data.user.username);
  emit({ status: "authed", user: data.user, workspaces: data.workspaces ?? [] });
  return data.user as AuthUser;
}

/** 刷新页面后校验本地令牌，拿不到用户就回到未登录态。 */
export function bootstrap(): Promise<void> {
  if (booting) return booting;
  if (!token) {
    if (state.status !== "anon") emit({ status: "anon", user: null, workspaces: [] });
    return Promise.resolve();
  }
  if (state.status === "authed") return Promise.resolve();

  emit({ ...state, status: "loading" });
  booting = (async () => {
    try {
      const res = await fetch("/api/auth/me");
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      emit({ status: "authed", user: data.user, workspaces: data.workspaces ?? [] });
    } catch {
      token = null;
      write(TOKEN_KEY, null);
      emit({ status: "anon", user: null, workspaces: [] });
    } finally {
      booting = null;
    }
  })();
  return booting;
}

export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {
    /* 网络异常也要让本地下线 */
  }
  clearSession();
}

function redirectToLogin(): void {
  if (window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  window.location.replace(`/login?next=${encodeURIComponent(next)}`);
}

let installed = false;

/** 注入 Authorization 头，并统一处理 401。 */
export function installAuthHeader(): void {
  if (installed) return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;

    if (!url.startsWith("/api/")) return original(input, init);

    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await original(input, { ...init, headers });

    if (response.status === 401 && !url.startsWith("/api/auth/login")) {
      clearSession();
      redirectToLogin();
    }
    return response;
  };
}
