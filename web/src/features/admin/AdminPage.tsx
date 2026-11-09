import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { MembersCard } from "./MembersCard";
import { InvitesCard } from "./InvitesCard";
import { MemoryCard } from "./MemoryCard";
import { confirmDialog } from "@/lib/confirm";

interface Summary {
  requests: {
    total: number;
    errors: number;
    avg_ms: number;
    p95_ms: number;
    top_paths: { path: string; count: number; avg_ms: number; errors: number }[];
  };
  llm: {
    total: number;
    avg_ms: number;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    by_kind: Record<string, { count: number; avg_ms: number; errors: number }>;
  };
}

interface ReqRecord {
  id: string;
  ts: string;
  method: string;
  path: string;
  status: number;
  duration_ms: number;
}

interface LlmRecord {
  id: string;
  ts: string;
  kind: string;
  model: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  duration_ms: number;
  status: string;
}

const KIND_LABEL: Record<string, string> = {
  chat: "对话",
  agent: "智能体",
  embedding: "嵌入",
  eval: "评测",
};

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="flex-1 p-4">
      <p className="text-xs text-tertiary">{label}</p>
      <p className="mt-1 text-xl font-medium text-primary">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-tertiary">{sub}</p>}
    </Card>
  );
}

export function AdminPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [requests, setRequests] = useState<ReqRecord[]>([]);
  const [llm, setLlm] = useState<LlmRecord[]>([]);

  const load = useCallback(async () => {
    const [s, r, l] = await Promise.all([
      fetch("/api/obs/summary").then((res) => res.json()),
      fetch("/api/obs/requests?limit=30").then((res) => res.json()),
      fetch("/api/obs/llm?limit=30").then((res) => res.json()),
    ]);
    setSummary(s);
    setRequests(r);
    setLlm(l);
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  const clear = async () => {
    const ok = await confirmDialog({
      title: "清空指标",
      message: "清空全部指标数据？此操作不可恢复。",
      confirmLabel: "清空",
      danger: true,
    });
    if (!ok) return;
    await fetch("/api/obs", { method: "DELETE" });
    await load();
  };

  const errorRate =
    summary && summary.requests.total > 0
      ? ((summary.requests.errors / summary.requests.total) * 100).toFixed(1) + "%"
      : "—";
  const tokens =
    summary?.llm.prompt_tokens != null
      ? `${(summary.llm.prompt_tokens / 1000).toFixed(1)}k / ${((summary.llm.completion_tokens ?? 0) / 1000).toFixed(1)}k`
      : "—";

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[960px] space-y-6 px-8 py-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-lg font-medium">系统管理 · 可观测</h1>
            <p className="mt-1 text-[13px] text-secondary">
              API 请求与模型调用指标，每 5 秒自动刷新。指标数据保留最近 2000 条请求 / 1000 次调用。
            </p>
          </div>
          <Button variant="ghost" onClick={() => void clear()}>
            清空指标
          </Button>
        </div>

        {/* 成员表有 6 列，半宽卡片会挤压换行；与邀请码卡片上下通栏排布 */}
        <MembersCard />
        <InvitesCard />

        <MemoryCard />

        {summary && (
          <>
            <div className="flex gap-3">
              <Metric label="API 请求" value={String(summary.requests.total)} sub={`错误 ${summary.requests.errors} · 错误率 ${errorRate}`} />
              <Metric label="平均延迟" value={`${summary.requests.avg_ms} ms`} sub={`P95 ${summary.requests.p95_ms} ms`} />
              <Metric label="模型调用" value={String(summary.llm.total)} sub={`平均 ${summary.llm.avg_ms} ms`} />
              <Metric label="Token (入/出)" value={tokens} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* 热点路径 */}
              <Card className="p-4">
                <h3 className="text-[13px] font-medium">热点路径</h3>
                <table className="mt-2 w-full text-xs">
                  <thead>
                    <tr className="text-left text-tertiary">
                      <th className="pb-1 font-normal">路径</th>
                      <th className="pb-1 text-right font-normal">次数</th>
                      <th className="pb-1 text-right font-normal">均延迟</th>
                      <th className="pb-1 text-right font-normal">错误</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.requests.top_paths.map((p) => (
                      <tr key={p.path} className="border-t border-line">
                        <td className="max-w-[220px] truncate py-1.5 text-secondary" title={p.path}>
                          {p.path}
                        </td>
                        <td className="py-1.5 text-right text-secondary">{p.count}</td>
                        <td className="py-1.5 text-right text-secondary">{p.avg_ms} ms</td>
                        <td className="py-1.5 text-right">
                          {p.errors > 0 ? (
                            <span className="text-danger">{p.errors}</span>
                          ) : (
                            <span className="text-tertiary">0</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>

              {/* 模型调用分类 */}
              <Card className="p-4">
                <h3 className="text-[13px] font-medium">模型调用</h3>
                <table className="mt-2 w-full text-xs">
                  <thead>
                    <tr className="text-left text-tertiary">
                      <th className="pb-1 font-normal">类型</th>
                      <th className="pb-1 text-right font-normal">次数</th>
                      <th className="pb-1 text-right font-normal">均耗时</th>
                      <th className="pb-1 text-right font-normal">错误</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(summary.llm.by_kind).map(([kind, a]) => (
                      <tr key={kind} className="border-t border-line">
                        <td className="py-1.5 text-secondary">{KIND_LABEL[kind] ?? kind}</td>
                        <td className="py-1.5 text-right text-secondary">{a.count}</td>
                        <td className="py-1.5 text-right text-secondary">{a.avg_ms} ms</td>
                        <td className="py-1.5 text-right">
                          {a.errors > 0 ? (
                            <span className="text-danger">{a.errors}</span>
                          ) : (
                            <span className="text-tertiary">0</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {Object.keys(summary.llm.by_kind).length === 0 && (
                      <tr>
                        <td colSpan={4} className="py-4 text-center text-tertiary">
                          暂无模型调用
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </Card>
            </div>
          </>
        )}

        {/* 最近请求 */}
        <Card className="p-4">
          <h3 className="text-[13px] font-medium">最近请求</h3>
          <div className="mt-2 max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-1">
                <tr className="text-left text-tertiary">
                  <th className="pb-1 font-normal">时间 (UTC)</th>
                  <th className="pb-1 font-normal">方法</th>
                  <th className="pb-1 font-normal">路径</th>
                  <th className="pb-1 text-right font-normal">状态</th>
                  <th className="pb-1 text-right font-normal">耗时</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id} className="border-t border-line">
                    <td className="py-1.5 text-tertiary">{r.ts}</td>
                    <td className="py-1.5 text-secondary">{r.method}</td>
                    <td className="max-w-[320px] truncate py-1.5 text-secondary" title={r.path}>
                      {r.path}
                    </td>
                    <td className={`py-1.5 text-right ${r.status >= 400 ? "text-danger" : "text-tertiary"}`}>
                      {r.status}
                    </td>
                    <td className="py-1.5 text-right text-secondary">{r.duration_ms} ms</td>
                  </tr>
                ))}
                {requests.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-tertiary">
                      暂无请求记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>

        {/* 最近模型调用 */}
        <Card className="p-4">
          <h3 className="text-[13px] font-medium">最近模型调用</h3>
          <div className="mt-2 max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-1">
                <tr className="text-left text-tertiary">
                  <th className="pb-1 font-normal">时间 (UTC)</th>
                  <th className="pb-1 font-normal">类型</th>
                  <th className="pb-1 font-normal">模型</th>
                  <th className="pb-1 text-right font-normal">入/出 Token</th>
                  <th className="pb-1 text-right font-normal">耗时</th>
                  <th className="pb-1 text-right font-normal">状态</th>
                </tr>
              </thead>
              <tbody>
                {llm.map((c) => (
                  <tr key={c.id} className="border-t border-line">
                    <td className="py-1.5 text-tertiary">{c.ts}</td>
                    <td className="py-1.5 text-secondary">{KIND_LABEL[c.kind] ?? c.kind}</td>
                    <td className="max-w-[200px] truncate py-1.5 text-secondary" title={c.model}>
                      {c.model}
                    </td>
                    <td className="py-1.5 text-right text-secondary">
                      {c.prompt_tokens != null ? `${c.prompt_tokens} / ${c.completion_tokens ?? 0}` : "—"}
                    </td>
                    <td className="py-1.5 text-right text-secondary">{c.duration_ms} ms</td>
                    <td className={`py-1.5 text-right ${c.status !== "ok" ? "text-danger" : "text-tertiary"}`}>
                      {c.status}
                    </td>
                  </tr>
                ))}
                {llm.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-4 text-center text-tertiary">
                      暂无模型调用记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
