import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface McpToolDef {
  name: string;
  description: string;
}

interface McpServer {
  id: string;
  name: string;
  transport: "stdio" | "http";
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  enabled: boolean;
  status: "unknown" | "ok" | "error";
  tools: McpToolDef[];
  last_error: string;
  refreshed_at: string;
  connection: string;
}

const EMPTY_FORM = {
  name: "",
  transport: "stdio" as "stdio" | "http",
  command: "",
  args: "",
  url: "",
};

const STATUS_STYLE: Record<McpServer["status"], { label: string; cls: string }> = {
  ok: { label: "已连接", cls: "bg-brand-soft text-brand" },
  error: { label: "连接失败", cls: "bg-danger/10 text-danger" },
  unknown: { label: "未刷新", cls: "bg-surface-3 text-tertiary" },
};

export function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/mcp/servers");
    setServers(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!form.name.trim()) {
      setError("名称不能为空");
      return;
    }
    if (form.transport === "stdio" && !form.command.trim()) {
      setError("stdio 传输需要填写启动命令");
      return;
    }
    if (form.transport === "http" && !form.url.trim()) {
      setError("http 传输需要填写服务地址");
      return;
    }
    const body = {
      name: form.name.trim(),
      transport: form.transport,
      command: form.command.trim(),
      args: form.args.trim() ? form.args.trim().split(/\s+/) : [],
      url: form.url.trim(),
    };
    const res = await fetch("/api/mcp/servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      setError(data?.detail ?? `注册失败：HTTP ${res.status}`);
      return;
    }
    setForm(EMPTY_FORM);
    setAdding(false);
    setError(null);
    await load();
  };

  const refresh = async (s: McpServer) => {
    setBusy(s.id);
    try {
      await fetch(`/api/mcp/servers/${s.id}/refresh`, { method: "POST" });
      await load();
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (s: McpServer) => {
    await fetch(`/api/mcp/servers/${s.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !s.enabled }),
    });
    await load();
  };

  const remove = async (s: McpServer) => {
    if (!window.confirm(`删除 MCP 服务器「${s.name}」？其工具将不再对智能体可见。`)) return;
    await fetch(`/api/mcp/servers/${s.id}`, { method: "DELETE" });
    await load();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[860px] space-y-6 px-8 py-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-lg font-medium">MCP 服务</h1>
            <p className="mt-1 text-[13px] text-secondary">
              注册外部 MCP Server，工具同步后与内置工具同构挂载给智能体。
              支持 stdio 与 streamable http 两种传输。
            </p>
          </div>
          <Button onClick={() => { setAdding(true); setError(null); }}>注册服务器</Button>
        </div>

        {/* 注册表单 */}
        {adding && (
          <Card className="space-y-3 p-5">
            <h3 className="text-[13px] font-medium">注册 MCP 服务器</h3>
            <div className="flex items-center gap-2">
              {(["stdio", "http"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setForm({ ...form, transport: t })}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-xs transition-colors duration-150",
                    form.transport === t
                      ? "border-brand bg-brand-soft text-brand"
                      : "border-line text-secondary hover:border-line-strong",
                  )}
                >
                  {t === "stdio" ? "stdio 子进程" : "streamable http"}
                </button>
              ))}
            </div>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="名称，如：文件系统工具"
              className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            {form.transport === "stdio" ? (
              <div className="grid grid-cols-[1fr_1fr] gap-3">
                <input
                  value={form.command}
                  onChange={(e) => setForm({ ...form, command: e.target.value })}
                  placeholder="启动命令，如：python 或 npx"
                  className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
                />
                <input
                  value={form.args}
                  onChange={(e) => setForm({ ...form, args: e.target.value })}
                  placeholder="参数（空格分隔），如：tools/mock_mcp_server.py"
                  className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
                />
              </div>
            ) : (
              <input
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                placeholder="服务地址，如：http://127.0.0.1:9000/mcp"
                className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
            )}
            {error && <p className="text-xs text-danger">{error}</p>}
            <div className="flex gap-2">
              <Button onClick={() => void add()}>注册并连接</Button>
              <Button variant="ghost" onClick={() => { setAdding(false); setError(null); }}>
                取消
              </Button>
            </div>
          </Card>
        )}

        {/* 服务器列表 */}
        <div className="space-y-2">
          {servers.length === 0 && !adding && (
            <Card className="p-8 text-center text-[13px] text-tertiary">
              还没有注册 MCP 服务器。注册后点击「刷新工具」同步工具清单。
            </Card>
          )}
          {servers.map((s) => {
            const status = STATUS_STYLE[s.status];
            return (
              <Card key={s.id} className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-primary">{s.name}</span>
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary">
                        {s.transport === "stdio" ? "stdio" : "http"}
                      </span>
                      <span className={cn("rounded px-1.5 py-0.5 text-xs", status.cls)}>
                        {status.label}
                      </span>
                      {!s.enabled && (
                        <span className="rounded bg-danger/10 px-1.5 py-0.5 text-xs text-danger">
                          已停用
                        </span>
                      )}
                    </div>
                    <p className="mt-1 truncate text-xs text-tertiary" title={s.connection}>
                      {s.connection}
                    </p>
                    {s.status === "error" && s.last_error && (
                      <p className="mt-1 line-clamp-2 text-xs text-danger">{s.last_error}</p>
                    )}
                    {s.refreshed_at && (
                      <p className="mt-1 text-xs text-tertiary">工具同步于 {s.refreshed_at} UTC</p>
                    )}
                    {s.tools.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {s.tools.map((t) => (
                          <span
                            key={t.name}
                            className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary"
                            title={t.description}
                          >
                            {t.name}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      disabled={busy === s.id}
                      onClick={() => void refresh(s)}
                    >
                      {busy === s.id ? "连接中…" : "刷新工具"}
                    </Button>
                    <Button variant="ghost" onClick={() => void toggle(s)}>
                      {s.enabled ? "停用" : "启用"}
                    </Button>
                    <Button variant="ghost" onClick={() => void remove(s)}>
                      删除
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    </div>
  );
}
