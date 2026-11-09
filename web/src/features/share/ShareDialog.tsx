import { useCallback, useEffect, useState } from "react";
import { Copy, ExternalLink, Link2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  createShare,
  embedSnippet,
  listShares,
  revokeShare,
  shareUrl,
  type ShareItem,
  type ShareKind,
} from "./api";

interface Props {
  open: boolean;
  onClose: () => void;
  kind: ShareKind;
  targetId: string;
  title: string;
}

const EXPIRY_OPTIONS = [
  { label: "长期有效", value: 0 },
  { label: "7 天", value: 7 },
  { label: "30 天", value: 30 },
];

/**
 * 分享面板：建链接、复制链接或 iframe、撤销。
 *
 * 只列这一份内容的分享：同一个人可能有几十条分享，混在一起找"刚才发出去的那条"
 * 是灾难。
 */
export function ShareDialog({ open, onClose, kind, targetId, title }: Props) {
  const [items, setItems] = useState<ShareItem[]>([]);
  const [expires, setExpires] = useState(0);
  const [allowAsk, setAllowAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const all = await listShares();
      setItems(all.filter((s) => s.kind === kind && s.target_id === targetId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取分享列表失败");
    }
  }, [kind, targetId]);

  useEffect(() => {
    if (open) {
      setError(null);
      setCopied(null);
      void reload();
    }
  }, [open, reload]);

  if (!open) return null;

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await createShare({
        kind,
        target_id: targetId,
        title,
        allow_ask: kind === "kb" ? allowAsk : false,
        expires_days: expires,
      });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建分享失败");
    } finally {
      setBusy(false);
    }
  };

  const copy = async (text: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(tag);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      // 非 HTTPS / 无剪贴板权限时退化为选中：让对方手动复制，不假装成功
      window.prompt("复制下面的内容", text);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      onClick={onClose}
    >
      <Card
        className="flex max-h-[80vh] w-full max-w-[560px] flex-col gap-3 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[13px] font-medium">对外分享</h3>
            <p className="mt-0.5 truncate text-xs text-tertiary">{title}</p>
          </div>
          <button onClick={onClose} className="text-xs text-tertiary hover:text-primary">
            关闭
          </button>
        </div>

        <div className="rounded-lg border border-line bg-surface-1 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={expires}
              onChange={(e) => setExpires(Number(e.target.value))}
              className="h-8 rounded-lg border border-line bg-surface-2 px-2 text-xs outline-none"
            >
              {EXPIRY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {kind === "kb" && (
              <label className="flex items-center gap-1.5 text-xs text-secondary">
                <input
                  type="checkbox"
                  checked={allowAsk}
                  onChange={(e) => setAllowAsk(e.target.checked)}
                  className="accent-brand"
                />
                允许访客提问（会消耗模型额度）
              </label>
            )}
            <Button className="ml-auto h-8" disabled={busy} onClick={() => void create()}>
              生成链接
            </Button>
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-tertiary">
            {kind === "conversation"
              ? "拿到链接的人无需登录即可阅读这段对话，包含全部问答正文。"
              : "拿到链接的人无需登录即可查看这个知识库的问答入口；不开提问时只有标题与说明。"}
          </p>
        </div>

        {error && <p className="text-xs text-danger">{error}</p>}

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          {items.length === 0 && (
            <p className="py-6 text-center text-xs text-tertiary">还没有分享记录</p>
          )}
          {items.map((s) => (
            <div key={s.id} className="rounded-lg border border-line p-3">
              <div className="flex items-center gap-2">
                <Link2 size={13} className={s.live ? "text-brand" : "text-tertiary"} />
                <span className="min-w-0 flex-1 truncate text-[12.5px]">{shareUrl(s.id)}</span>
                <span
                  className={
                    s.live
                      ? "shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] text-brand"
                      : "shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[11px] text-tertiary"
                  }
                >
                  {s.revoked ? "已撤销" : s.live ? "有效" : "已过期"}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-tertiary">
                <span>浏览 {s.view_count} 次</span>
                {s.allow_ask && <span>· 允许提问</span>}
                {s.expires_at && <span>· 到期 {s.expires_at.slice(0, 10)}</span>}
                <div className="ml-auto flex items-center gap-2">
                  <button
                    className="flex items-center gap-1 hover:text-brand"
                    onClick={() => void copy(shareUrl(s.id), `${s.id}-link`)}
                  >
                    <Copy size={12} />
                    {copied === `${s.id}-link` ? "已复制" : "复制链接"}
                  </button>
                  {kind === "kb" && (
                    <button
                      className="flex items-center gap-1 hover:text-brand"
                      onClick={() => void copy(embedSnippet(s.id), `${s.id}-embed`)}
                    >
                      <Copy size={12} />
                      {copied === `${s.id}-embed` ? "已复制" : "复制嵌入代码"}
                    </button>
                  )}
                  <a
                    href={`/s/${s.id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1 hover:text-brand"
                  >
                    <ExternalLink size={12} />
                    打开
                  </a>
                  {s.live && (
                    <button
                      className="flex items-center gap-1 hover:text-danger"
                      onClick={() => {
                        void revokeShare(s.id).then(reload);
                      }}
                    >
                      <Trash2 size={12} />
                      撤销
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
