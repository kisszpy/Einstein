/** 分享接口的类型与薄封装：组件只管渲染，取数都走这里。 */

export type ShareKind = "conversation" | "kb";

export interface ShareItem {
  id: string;
  kind: ShareKind;
  target_id: string;
  title: string;
  allow_ask: boolean;
  expires_at: string;
  revoked: boolean;
  live: boolean;
  view_count: number;
  last_viewed_at: string;
  created_at: string;
  note: string;
}

export interface PublicShare {
  kind: ShareKind;
  title: string;
  allow_ask: boolean;
  expires_at: string;
  created_at: string;
  conversation?: {
    title: string;
    created_at: string;
    updated_at: string;
    messages: { role: string; content: string; created_at: string }[];
  };
  kb?: { name: string; description: string };
}

export async function listShares(): Promise<ShareItem[]> {
  const res = await fetch("/api/shares");
  if (!res.ok) throw new Error("读取分享列表失败");
  return res.json();
}

export async function createShare(input: {
  kind: ShareKind;
  target_id: string;
  title?: string;
  allow_ask?: boolean;
  expires_days?: number;
  note?: string;
}): Promise<ShareItem> {
  const res = await fetch("/api/shares", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "创建分享失败");
  }
  return res.json();
}

export async function revokeShare(token: string): Promise<ShareItem> {
  const res = await fetch(`/api/shares/${token}`, { method: "DELETE" });
  if (!res.ok) throw new Error("撤销失败");
  return res.json();
}

/** 分享出去的绝对地址：token 是路径里唯一的东西。 */
export function shareUrl(token: string): string {
  return `${window.location.origin}/s/${token}`;
}

/** iframe 片段：给"嵌到公司门户/内网 wiki"的场景，固定高度避免塌陷。 */
export function embedSnippet(token: string, height = 640): string {
  return `<iframe src="${shareUrl(token)}" width="100%" height="${height}" style="border:1px solid #e5e7eb;border-radius:12px" loading="lazy"></iframe>`;
}
