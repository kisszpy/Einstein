import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Download, GitBranch, Loader2, Search, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { MarketResponse } from "./types";

interface Props {
  onInstall: (url: string) => void;
}

function compact(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function MarketPanel({ onInstall }: Props) {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<MarketResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (q: string) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`/api/skills/market?limit=20&q=${encodeURIComponent(q)}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "市场加载失败");
      setData(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "市场加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("");
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-tertiary" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void load(query)}
            placeholder="搜索技能仓库（名称、描述关键词）"
            className="h-9 w-full rounded-lg border border-line bg-surface-1 pl-9 pr-3 text-[13px] outline-none transition-colors duration-150 placeholder:text-tertiary focus:border-brand"
          />
        </div>
        <Button variant="outline" onClick={() => void load(query)} disabled={loading}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : "搜索"}
        </Button>
      </div>

      {data?.rate_limited && (
        <p className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[12px] text-warning">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          {data.note}
        </p>
      )}
      {data && !data.rate_limited && (
        <p className="text-[11.5px] text-tertiary">
          共 {data.total.toLocaleString()} 个仓库，按 star 排序
          {data.cached ? " · 缓存结果" : ""} · 安装前会先扫描仓库里的 SKILL.md
        </p>
      )}
      {error && <p className="text-[12.5px] text-danger">{error}</p>}

      <div className="space-y-2">
        {(data?.items ?? []).map((item) => (
          <Card key={item.key} className="p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate text-[13px] font-medium text-primary transition-colors duration-150 hover:text-brand"
                  >
                    {item.full_name || item.name}
                  </a>
                  {item.installed > 0 && (
                    <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] text-brand">
                      已装 {item.installed}
                    </span>
                  )}
                  {item.fallback && (
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px] text-tertiary">
                      精选
                    </span>
                  )}
                </div>
                <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-secondary">
                  {item.description || "（作者未填写描述）"}
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-tertiary">
                  {!item.fallback && (
                    <span className="flex items-center gap-1">
                      <Star size={11} />
                      {compact(item.stars)}
                    </span>
                  )}
                  <span className="flex items-center gap-1">
                    <GitBranch size={11} />
                    {item.author}
                  </span>
                  {item.topics.slice(0, 4).map((tag) => (
                    <span key={tag} className="rounded bg-surface-3 px-1.5 py-0.5">
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
              <Button
                variant="outline"
                className="shrink-0"
                onClick={() => onInstall(item.install_url)}
              >
                <Download size={13} className="mr-1.5" />
                安装
              </Button>
            </div>
          </Card>
        ))}
      </div>

      {!loading && data && data.items.length === 0 && (
        <p className="py-10 text-center text-[12.5px] text-tertiary">
          没有匹配的仓库，换个关键词，或直接把 GitHub 地址粘贴到「从 GitHub 安装」。
        </p>
      )}
    </div>
  );
}
