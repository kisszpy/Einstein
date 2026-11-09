import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiSend } from "./api";
import type { AlertKind, AlertRule } from "./types";

const INPUT_CLASS =
  "h-9 w-full rounded-lg border border-line bg-surface-1 px-2.5 text-[13px] outline-none transition-colors duration-150 focus:border-brand";

export function RuleDialog({
  rule,
  kinds,
  onClose,
  onSaved,
}: {
  rule: AlertRule | null;
  kinds: AlertKind[];
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const fallback = kinds[0];
  const [name, setName] = useState(rule?.name ?? "");
  const [kind, setKind] = useState(rule?.kind ?? fallback?.kind ?? "error_rate");
  const [threshold, setThreshold] = useState(String(rule?.threshold ?? fallback?.suggest ?? 0));
  const [windowHours, setWindowHours] = useState(String(rule?.window_hours ?? 24));
  const [cooldown, setCooldown] = useState(String(rule?.cooldown_minutes ?? 60));
  const [webhook, setWebhook] = useState(rule?.channels[0]?.url ?? "");
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const meta = kinds.find((k) => k.kind === kind);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const save = async () => {
    const t = Number(threshold);
    const w = Number(windowHours);
    const c = Number(cooldown);
    if (!name.trim()) {
      setError("名称不能为空");
      return;
    }
    if (!Number.isFinite(t) || t < 0) {
      setError("阈值需要是大于等于 0 的数字");
      return;
    }
    if (!Number.isInteger(w) || w < 1) {
      setError("窗口小时数需要是大于等于 1 的整数");
      return;
    }
    if (!Number.isInteger(c) || c < 0) {
      setError("冷却分钟需要是大于等于 0 的整数");
      return;
    }
    const url = webhook.trim();
    const body = {
      name: name.trim(),
      kind,
      threshold: t,
      window_hours: w,
      cooldown_minutes: c,
      channels: url ? [{ type: "webhook", url }] : [],
      enabled,
    };
    setBusy(true);
    setError(null);
    try {
      if (rule) {
        await apiSend(`/api/alerts/rules/${rule.id}`, "PATCH", body);
        onSaved("规则已更新");
      } else {
        await apiSend("/api/alerts/rules", "POST", body);
        onSaved("规则已创建");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      onClick={onClose}
    >
      <Card
        className="max-h-[85vh] w-full max-w-[520px] space-y-3 overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-[13px] font-medium">{rule ? "编辑规则" : "新建规则"}</h3>

        <Field label="名称">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如「线上错误率过高」"
            className={INPUT_CLASS}
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select
              value={kind}
              onChange={(e) => {
                const next = e.target.value;
                setKind(next);
                const target = kinds.find((k) => k.kind === next);
                if (target) setThreshold(String(target.suggest));
              }}
              className={INPUT_CLASS}
            >
              {kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={`阈值${meta ? `（${meta.unit}）` : ""}`}>
            <input
              type="number"
              min={0}
              step="any"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
        </div>
        {meta?.unit === "比例" && (
          <p className="-mt-1.5 text-[11px] text-tertiary">比例填小数，0.2 即 20%。</p>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="窗口（小时）">
            <input
              type="number"
              min={1}
              step={1}
              value={windowHours}
              onChange={(e) => setWindowHours(e.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="冷却（分钟）">
            <input
              type="number"
              min={0}
              step={1}
              value={cooldown}
              onChange={(e) => setCooldown(e.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
        </div>

        <Field label="Webhook 地址">
          <input
            value={webhook}
            onChange={(e) => setWebhook(e.target.value)}
            placeholder="https://example.com/hooks/einstein"
            className={INPUT_CLASS}
          />
          <p className="mt-1 text-[11px] text-tertiary">留空则只记录触发历史，不发通知。</p>
        </Field>

        <label className="flex items-center gap-2 text-[12.5px] text-secondary">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--brand)]"
          />
          启用这条规则
        </label>

        {error && <p className="text-xs text-danger">{error}</p>}

        <div className="flex gap-2 pt-1">
          <Button disabled={busy || !name.trim()} onClick={() => void save()}>
            {busy ? "保存中…" : "保存"}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
        </div>
      </Card>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11.5px] text-tertiary">{label}</span>
      {children}
    </label>
  );
}
