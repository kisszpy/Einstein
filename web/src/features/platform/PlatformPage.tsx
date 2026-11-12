import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageBody, PageHeader } from "@/components/ui/page-header";
import { shortTime } from "@/lib/utils";

interface ApiKeyRow {
  id: string;
  name: string;
  workspace_id: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
  call_count: number;
}

interface AuditEntry {
  ts: string;
  key_name: string | null;
  method: string;
  path: string;
  status: number;
  duration_ms: number;
  tokens_in: number;
  tokens_out: number;
  detail: string;
}

interface AuditPayload {
  summary: { total: number; errors: number; tokens_in: number; tokens_out: number; keys: number };
  entries: AuditEntry[];
}

const CURL_SAMPLE = `curl http://127.0.0.1:8000/v1/chat/completions \\
  -H "Authorization: Bearer sk-你的密钥" \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"你好"}],"stream":false}'`;

export function PlatformPage() {
  const [keys, setKeys] = useState<ApiKeyRow[]>([]);
  const [audit, setAudit] = useState<AuditPayload | null>(null);
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [k, a] = await Promise.all([
      fetch("/api/apikeys").then((r) => r.json()),
      fetch("/api/audit?limit=50").then((r) => r.json()),
    ]);
    setKeys(k);
    setAudit(a);
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 8000);
    return () => clearInterval(timer);
  }, [load]);

  const issue = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    const res = await fetch("/api/apikeys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    setBusy(false);
    if (!res.ok) return;
    const data = await res.json();
    setIssued(data.token);
    setName("");
    await load();
  };

  const revoke = async (id: string) => {
    await fetch(`/api/apikeys/${id}`, { method: "DELETE" });
    await load();
  };

  return (
    <div className="h-full overflow-y-auto">
      <PageBody width="narrow" className="space-y-6">
        <PageHeader
          title="开放平台"
          description="API Key 绑定当前工作区：用密钥调用 /v1 接口时，只能访问该工作区自己的知识库与专家，所有调用都会写入审计日志。"
        />

        {/* 密钥管理 */}
        <Card className="p-5">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-[13px] font-medium">API Key</h3>
              <p className="mt-0.5 text-xs text-tertiary">
                明文只在创建时显示一次，服务端只存摘要。
              </p>
            </div>
            <div className="flex gap-2">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void issue()}
                placeholder="密钥用途，如「报表系统」"
                className="h-8 w-52 rounded-lg border border-line bg-surface-1 px-2.5 text-[13px] outline-none focus:border-line-strong"
              />
              <Button disabled={busy || !name.trim()} onClick={() => void issue()}>
                签发
              </Button>
            </div>
          </div>

          {issued && (
            <div className="mt-3 rounded-lg border border-brand bg-brand-soft p-3">
              <p className="text-xs text-brand-ink ">密钥已创建，请立即复制（关闭后不再显示）</p>
              <div className="mt-1.5 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-surface-1 px-2 py-1 text-xs text-primary">
                  {issued}
                </code>
                <Button variant="ghost" onClick={() => setIssued(null)}>
                  已复制
                </Button>
              </div>
            </div>
          )}

          <table className="mt-4 w-full text-xs">
            <thead>
              <tr className="text-left text-tertiary">
                <th className="pb-1 font-normal">名称</th>
                <th className="pb-1 font-normal">密钥</th>
                <th className="pb-1 text-right font-normal">调用</th>
                <th className="pb-1 font-normal">最近使用</th>
                <th className="pb-1 text-right font-normal">状态</th>
                <th className="pb-1 text-right font-normal"></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-t border-line">
                  <td className="py-2 text-secondary">{k.name}</td>
                  <td className="py-2 font-mono text-tertiary">{k.prefix}</td>
                  <td className="py-2 text-right text-secondary">{k.call_count}</td>
                  <td className="py-2 text-tertiary">{k.last_used_at ?? "—"}</td>
                  <td className="py-2 text-right">
                    <span className={k.revoked ? "text-danger" : "text-success"}>
                      {k.revoked ? "已吊销" : "有效"}
                    </span>
                  </td>
                  <td className="py-2 text-right">
                    {!k.revoked && (
                      <button
                        onClick={() => void revoke(k.id)}
                        className="text-xs text-tertiary transition-colors hover:text-danger"
                      >
                        吊销
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-tertiary">
                    还没有密钥
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>

        {/* 调用方式 */}
        <Card className="p-5">
          <h3 className="text-[13px] font-medium">调用方式</h3>
          <p className="mt-0.5 text-xs text-tertiary">
            兼容 OpenAI Chat Completions；额外支持 <code>kb_ids</code> / <code>expert_id</code> 字段挂载知识库与专家。
            另有 <code>/v1/search</code>（纯检索）与 <code>/v1/agent/run</code>（智能体）。
          </p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-surface-3 p-3 text-[12px] leading-relaxed text-secondary">
            {CURL_SAMPLE}
          </pre>
        </Card>

        {/* 审计 */}
        <Card className="p-5">
          <div className="flex items-baseline justify-between">
            <h3 className="text-[13px] font-medium">调用审计</h3>
            <div className="flex items-center gap-2">
              {audit && (
                <span className="text-xs text-tertiary">
                  {audit.summary.total} 次调用 · 错误 {audit.summary.errors} · Token{" "}
                  {audit.summary.tokens_in}/{audit.summary.tokens_out}
                </span>
              )}
              <a
                href="/api/audit/export.csv"
                className="rounded-md border border-line px-2.5 py-1.5 text-xs text-secondary transition-colors duration-150 hover:border-line-strong hover:text-primary"
              >
                导出 CSV
              </a>
            </div>
          </div>
          <div className="mt-2 max-h-80 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-2">
                <tr className="text-left text-tertiary">
                  <th className="pb-1 font-normal">时间</th>
                  <th className="pb-1 font-normal">密钥</th>
                  <th className="pb-1 font-normal">接口</th>
                  <th className="pb-1 text-right font-normal">状态</th>
                  <th className="pb-1 text-right font-normal">耗时</th>
                  <th className="pb-1 text-right font-normal">Token 入/出</th>
                </tr>
              </thead>
              <tbody>
                {audit?.entries.map((e, i) => (
                  <tr key={`${e.ts}-${i}`} className="border-t border-line">
                    <td className="py-1.5 tabular-nums text-tertiary">{shortTime(e.ts)}</td>
                    <td className="max-w-[120px] truncate py-1.5 text-secondary">
                      {e.key_name ?? "—"}
                    </td>
                    <td className="max-w-[220px] truncate py-1.5 font-mono text-secondary">
                      {e.method} {e.path}
                    </td>
                    <td className={`py-1.5 text-right ${e.status >= 400 ? "text-danger" : "text-tertiary"}`}>
                      {e.status}
                    </td>
                    <td className="py-1.5 text-right text-secondary">{e.duration_ms} ms</td>
                    <td className="py-1.5 text-right text-secondary">
                      {e.tokens_in}/{e.tokens_out}
                    </td>
                  </tr>
                ))}
                {!audit?.entries.length && (
                  <tr>
                    <td colSpan={6} className="py-4 text-center text-tertiary">
                      还没有外部调用记录
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </PageBody>
    </div>
  );
}
