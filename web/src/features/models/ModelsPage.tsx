/**
 * 模型管理：登记对话 / 向量端点，检测连通性，指定默认模型。
 *
 * 默认模型至多一个：`is_default` 是互斥开关，设新的会自动取消旧的；一个默认
 * 都没有时全站回落到 .env，因此列表顶部保留 .env 那一档只读行——看不到它，
 * 就分不清「现在到底在用哪个端点」。
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Cpu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageBody, PageHeader } from "@/components/ui/page-header";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";

interface ModelRow {
  id: string;
  name: string;
  base_url: string;
  model: string;
  has_key: boolean;
  key_hint: string;
  context_window: number;
  max_output: number;
  temperature: number;
  kind: "chat" | "embed";
  is_default: boolean;
  enabled: boolean;
  tags: string[];
  note: string;
  last_status: "unknown" | "ok" | "error";
  last_error: string;
  last_checked_at: string;
  owner_id: string;
  workspace_id: string;
  created_at: string;
  updated_at: string;
  readonly?: boolean;
}

interface ModelsPayload {
  items: ModelRow[];
  env: ModelRow;
}

type Kind = "chat" | "embed";

interface FormState {
  name: string;
  base_url: string;
  model: string;
  api_key: string;
  context_window: string;
  max_output: string;
  temperature: number;
  kind: Kind;
  is_default: boolean;
  platform: boolean;
  tags: string;
  note: string;
  clear_key: boolean;
}

interface Probe {
  ok: boolean;
  latency_ms: number;
  error: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  base_url: "",
  model: "",
  api_key: "",
  context_window: "",
  max_output: "",
  temperature: 0.7,
  kind: "chat",
  is_default: false,
  platform: false,
  tags: "",
  note: "",
  clear_key: false,
};

const INPUT_CLS =
  "h-9 w-full rounded-lg border border-line bg-surface-1 px-2.5 text-[13px] text-primary outline-none placeholder:text-placeholder focus:border-line-strong";

const TEXTAREA_CLS =
  "w-full resize-y rounded-lg border border-line bg-surface-1 px-2.5 py-2 text-[13px] text-primary outline-none placeholder:text-placeholder focus:border-line-strong";

async function readDetail(res: Response): Promise<string> {
  const data = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof data?.detail === "string" && data.detail ? data.detail : `请求失败（HTTP ${res.status}）`;
}

function fmtWhen(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function Badge({ tone = "muted", children }: { tone?: "muted" | "brand"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[12px]",
        tone === "brand" ? "bg-brand-soft text-brand-ink " : "bg-surface-3 text-tertiary",
      )}
    >
      {children}
    </span>
  );
}

function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1 flex items-baseline gap-1.5 text-xs text-tertiary">
        {label}
        {hint && <span className="text-[12px] text-tertiary">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

export function ModelsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const [items, setItems] = useState<ModelRow[]>([]);
  const [env, setEnv] = useState<ModelRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<{ id: string; action: "test" | "default" | "remove" } | null>(
    null,
  );
  const [probes, setProbes] = useState<Record<string, Probe>>({});

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const res = await fetch("/api/models");
      if (!res.ok) {
        setLoadError(await readDetail(res));
        return;
      }
      const data = (await res.json()) as ModelsPayload;
      setItems(Array.isArray(data.items) ? data.items : []);
      setEnv(data.env ?? null);
    } catch {
      setLoadError("无法连接后端，请确认服务已启动后重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Esc 关闭表单
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setEditing(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);

  const openCreate = () => {
    setEditing("new");
    setForm(EMPTY_FORM);
    setFormError("");
  };

  const openEdit = (row: ModelRow) => {
    setEditing(row.id);
    setForm({
      name: row.name,
      base_url: row.base_url,
      model: row.model,
      api_key: "",
      context_window: row.context_window ? String(row.context_window) : "",
      max_output: row.max_output ? String(row.max_output) : "",
      temperature: row.temperature,
      kind: row.kind === "embed" ? "embed" : "chat",
      is_default: row.is_default,
      platform: row.owner_id === "",
      tags: row.tags.join(", "),
      note: row.note,
      clear_key: false,
    });
    setFormError("");
  };

  const submit = async () => {
    const name = form.name.trim();
    const baseUrl = form.base_url.trim();
    const model = form.model.trim();
    if (!name) {
      setFormError("名称不能为空");
      return;
    }
    if (!/^https?:\/\//i.test(baseUrl)) {
      setFormError("base_url 需以 http:// 或 https:// 开头");
      return;
    }
    if (!model) {
      setFormError("model 不能为空");
      return;
    }

    const common = {
      name,
      base_url: baseUrl,
      model,
      context_window: Number(form.context_window) || 0,
      max_output: Number(form.max_output) || 0,
      temperature: form.temperature,
      kind: form.kind,
      is_default: form.is_default,
      tags: form.tags
        .split(/[,，、]/)
        .map((t) => t.trim())
        .filter(Boolean),
      note: form.note.trim(),
    };

    setSaving(true);
    setFormError("");
    try {
      if (editing === "new") {
        const res = await fetch("/api/models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...common,
            api_key: form.api_key.trim(),
            platform: isAdmin && form.platform,
          }),
        });
        if (!res.ok) {
          setFormError(await readDetail(res));
          return;
        }
      } else {
        // api_key 留空表示不修改；要清空必须显式传 clear_key
        const body: Record<string, unknown> = { ...common };
        if (form.api_key.trim()) body.api_key = form.api_key.trim();
        if (form.clear_key) body.clear_key = true;
        const res = await fetch(`/api/models/${editing}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          setFormError(await readDetail(res));
          return;
        }
      }
    } catch {
      setFormError("保存失败：无法连接后端");
      return;
    } finally {
      setSaving(false);
    }

    setEditing(null);
    await load();
  };

  const test = async (row: ModelRow) => {
    if (busy) return;
    setBusy({ id: row.id, action: "test" });
    setActionError("");
    try {
      const res = await fetch(`/api/models/${row.id}/test`, { method: "POST" });
      if (!res.ok) {
        const detail = await readDetail(res);
        setProbes((p) => ({ ...p, [row.id]: { ok: false, latency_ms: 0, error: detail } }));
        return;
      }
      const data = (await res.json()) as {
        ok: boolean;
        latency_ms: number;
        error: string;
        model: ModelRow;
      };
      setProbes((p) => ({
        ...p,
        [row.id]: { ok: data.ok, latency_ms: data.latency_ms, error: data.error },
      }));
      if (data.model?.id) {
        const next = data.model;
        setItems((list) => list.map((m) => (m.id === next.id ? next : m)));
      }
    } catch {
      setProbes((p) => ({
        ...p,
        [row.id]: { ok: false, latency_ms: 0, error: "请求失败，请检查网络或后端是否在运行" },
      }));
    } finally {
      setBusy(null);
    }
  };

  const setDefault = async (row: ModelRow) => {
    setBusy({ id: row.id, action: "default" });
    setActionError("");
    try {
      const res = await fetch(`/api/models/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_default: true }),
      });
      if (!res.ok) {
        setActionError(await readDetail(res));
        return;
      }
      await load();
    } catch {
      setActionError("设为默认失败：无法连接后端");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (row: ModelRow) => {
    const ok = await confirmDialog({
      title: "删除模型",
      message: `删除模型「${row.name}」？引用它的专家会回到「跟随平台默认」。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusy({ id: row.id, action: "remove" });
    setActionError("");
    try {
      const res = await fetch(`/api/models/${row.id}`, { method: "DELETE" });
      if (!res.ok) {
        setActionError(await readDetail(res));
        return;
      }
      await load();
    } catch {
      setActionError("删除失败：无法连接后端");
    } finally {
      setBusy(null);
    }
  };

  const editingRow = editing && editing !== "new" ? items.find((m) => m.id === editing) ?? null : null;

  return (
    <div className="h-full overflow-y-auto">
      <PageBody width="narrow">
        <PageHeader
          title="模型"
          description="对话与智能体实际调用的端点。留空则由 .env 决定。"
          actions={<Button onClick={openCreate}>添加模型</Button>}
        />

        {loadError && (
          <Card className="flex items-center justify-between gap-3 p-4">
            <p className="text-xs text-danger">加载失败：{loadError}</p>
            <Button variant="ghost" onClick={() => void load()}>
              重试
            </Button>
          </Card>
        )}

        {actionError && <p className="text-xs text-danger">{actionError}</p>}

        {loading && <p className="text-xs text-tertiary">加载中…</p>}

        {/* .env 那一档：只读，作为「没有默认模型」时的兜底展示 */}
        {env && (
          <Card className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-medium text-primary">{env.name}</span>
              <Badge>环境变量</Badge>
              {env.is_default && <Badge tone="brand">当前默认</Badge>}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-tertiary">
              <code className="font-mono">{env.model}</code>
              <span className="min-w-0 truncate font-mono" title={env.base_url}>
                {env.base_url}
              </span>
            </div>
            {env.note && <p className="mt-1.5 text-xs text-tertiary">{env.note}</p>}
          </Card>
        )}

        {!loading && !loadError && items.length === 0 && (
          <EmptyState
            icon={Cpu}
            title="还没有登记模型"
            description="所有调用当前走 .env 里的默认端点。登记一个自己的端点后，可以按工作区隔离调用与计费。"
            action={<Button onClick={openCreate}>添加第一个模型</Button>}
          />
        )}

        {items.map((row) => {
          const probe = probes[row.id];
          const testing = busy?.id === row.id && busy.action === "test";
          const defaulting = busy?.id === row.id && busy.action === "default";
          const removing = busy?.id === row.id && busy.action === "remove";
          // 预置模型（owner_id 为空）只有管理员能改
          const canEdit = !row.readonly && (row.owner_id !== "" || isAdmin);
          const checkedAt = fmtWhen(row.last_checked_at);
          return (
            <Card key={row.id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium text-primary">{row.name}</span>
                    <code className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[12px] text-secondary">
                      {row.model}
                    </code>
                    {row.is_default && <Badge tone="brand">默认</Badge>}
                    {row.kind === "embed" && <Badge>向量</Badge>}
                    {!row.enabled && <Badge>已停用</Badge>}
                  </div>

                  <p className="mt-1 truncate font-mono text-xs text-tertiary" title={row.base_url}>
                    {row.base_url}
                  </p>

                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                    <span className="text-secondary">
                      密钥：{row.has_key ? `已配置 ${row.key_hint}` : "未配置（回落到 .env 密钥）"}
                    </span>
                    <span className="flex items-center gap-1.5 text-secondary">
                      <span
                        aria-hidden
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          row.last_status === "ok"
                            ? "bg-success"
                            : row.last_status === "error"
                              ? "bg-danger"
                              : "bg-tertiary",
                        )}
                      />
                      {row.last_status === "ok"
                        ? `可用${checkedAt ? ` · 检测于 ${checkedAt}` : ""}`
                        : row.last_status === "error"
                          ? `不可用${checkedAt ? ` · 检测于 ${checkedAt}` : ""}`
                          : "未检测"}
                    </span>
                    <span className="text-tertiary">
                      上下文 {row.context_window || "—"} · 最大输出 {row.max_output || "—"} · 温度{" "}
                      {row.temperature.toFixed(1)}
                    </span>
                  </div>

                  {row.last_status === "error" && row.last_error && (
                    <p className="mt-1 text-xs text-danger">{row.last_error}</p>
                  )}

                  {/* 检测失败时行内的 last_error 已经是同一段原因，不重复一遍 */}
                  {probe && (probe.ok || probe.error !== row.last_error) && (
                    <p className={cn("mt-1 text-xs", probe.ok ? "text-success" : "text-danger")}>
                      {probe.ok ? `检测通过 · ${probe.latency_ms} ms` : `检测失败 · ${probe.error}`}
                    </p>
                  )}

                  {row.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {row.tags.map((t) => (
                        <span
                          key={t}
                          className="rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-tertiary"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {!row.readonly && (
                  <div className="flex shrink-0 flex-wrap items-center gap-1">
                    <Button
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() => void test(row)}
                    >
                      {testing ? "检测中…" : "检测"}
                    </Button>
                    {canEdit && !row.is_default && row.enabled && (
                      <Button
                        variant="ghost"
                        disabled={busy !== null}
                        onClick={() => void setDefault(row)}
                      >
                        {defaulting ? "设置中…" : "设为默认"}
                      </Button>
                    )}
                    {canEdit && (
                      <>
                        <Button variant="ghost" onClick={() => openEdit(row)}>
                          编辑
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={busy !== null}
                          onClick={() => void remove(row)}
                        >
                          {removing ? "删除中…" : "删除"}
                        </Button>
                      </>
                    )}
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </PageBody>

      {/* 添加 / 编辑：项目里没有 Modal，遮罩 + Esc 自己处理 */}
      {editing && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-[8vh]"
          role="presentation"
          onMouseDown={() => setEditing(null)}
        >
          <Card
            role="dialog"
            aria-modal="true"
            className="w-full max-w-[640px] space-y-3 p-5 shadow-float"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h3 className="text-[13px] font-medium">
              {editing === "new" ? "添加模型" : "编辑模型"}
            </h3>

            <div className="grid grid-cols-2 gap-3">
              <Field label="名称">
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="如「本地 27B」"
                  className={INPUT_CLS}
                />
              </Field>
              <Field label="类型">
                <select
                  value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value as Kind })}
                  className={INPUT_CLS}
                >
                  <option value="chat">chat（对话）</option>
                  <option value="embed">embed（向量）</option>
                </select>
              </Field>

              <Field label="base_url" className="col-span-2">
                <input
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  placeholder="http://127.0.0.1:8080/v1"
                  className={cn(INPUT_CLS, "font-mono")}
                />
              </Field>

              <Field label="model">
                <input
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  placeholder="qwen3.6-27b"
                  className={cn(INPUT_CLS, "font-mono")}
                />
              </Field>

              <Field
                label="api_key"
                hint={editing === "new" ? "留空回落到 .env 密钥" : undefined}
              >
                <input
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  disabled={form.clear_key}
                  placeholder={editing === "new" ? "sk-…" : "留空则不修改"}
                  className={INPUT_CLS}
                />
              </Field>

              {editing !== "new" && editingRow?.has_key && (
                <label className="col-span-2 flex items-center gap-2 text-xs text-secondary">
                  <input
                    type="checkbox"
                    checked={form.clear_key}
                    onChange={(e) => setForm({ ...form, clear_key: e.target.checked })}
                    className="accent-[var(--brand)]"
                  />
                  清除已配置的密钥（之后回落到 .env 密钥）
                </label>
              )}

              <Field label="上下文窗口" hint="token 数">
                <input
                  type="number"
                  min={0}
                  value={form.context_window}
                  onChange={(e) => setForm({ ...form, context_window: e.target.value })}
                  placeholder="32768"
                  className={INPUT_CLS}
                />
              </Field>
              <Field label="最大输出" hint="token 数">
                <input
                  type="number"
                  min={0}
                  value={form.max_output}
                  onChange={(e) => setForm({ ...form, max_output: e.target.value })}
                  placeholder="4096"
                  className={INPUT_CLS}
                />
              </Field>

              <Field label="温度" className="col-span-2">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={2}
                    step={0.1}
                    value={form.temperature}
                    onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
                    className="w-full accent-[var(--brand)]"
                  />
                  <span className="w-8 shrink-0 text-xs text-secondary">
                    {form.temperature.toFixed(1)}
                  </span>
                </div>
              </Field>

              <Field label="标签" hint="逗号分隔" className="col-span-2">
                <input
                  value={form.tags}
                  onChange={(e) => setForm({ ...form, tags: e.target.value })}
                  placeholder="本地, 27B"
                  className={INPUT_CLS}
                />
              </Field>

              <Field label="备注" className="col-span-2">
                <textarea
                  value={form.note}
                  onChange={(e) => setForm({ ...form, note: e.target.value })}
                  rows={2}
                  placeholder="这条模型是做什么用的、有什么限制"
                  className={TEXTAREA_CLS}
                />
              </Field>
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-xs text-secondary">
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                  className="accent-[var(--brand)]"
                />
                设为默认（全站至多一个）
              </label>
              {editing === "new" && isAdmin && (
                <label className="flex items-center gap-2 text-xs text-secondary">
                  <input
                    type="checkbox"
                    checked={form.platform}
                    onChange={(e) => setForm({ ...form, platform: e.target.checked })}
                    className="accent-[var(--brand)]"
                  />
                  平台预置（全员可见）
                </label>
              )}
              {editing !== "new" && editingRow?.owner_id === "" && (
                <span className="text-[12px] text-tertiary">平台预置模型</span>
              )}
            </div>

            {formError && <p className="text-xs text-danger">{formError}</p>}

            <div className="flex gap-2">
              <Button disabled={saving} onClick={() => void submit()}>
                {saving ? "保存中…" : "保存"}
              </Button>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                取消
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
