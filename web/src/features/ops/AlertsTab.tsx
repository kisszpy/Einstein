import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiDelete, apiGet, apiSend } from "./api";
import { conditionText, timeText } from "./format";
import { RuleDialog } from "./RuleDialog";
import type { AlertEvent, AlertKind, AlertRule, CheckResult } from "./types";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";

export function AlertsTab() {
  const [kinds, setKinds] = useState<AlertKind[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [checking, setChecking] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [testingId, setTestingId] = useState("");
  const [dialog, setDialog] = useState<{ rule: AlertRule | null } | null>(null);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    const [k, r, e] = await Promise.all([
      apiGet<AlertKind[]>("/api/alerts/kinds"),
      apiGet<AlertRule[]>("/api/alerts/rules"),
      apiGet<AlertEvent[]>("/api/alerts/events?limit=50"),
    ]);
    setKinds(k);
    setRules(r);
    setEvents(e);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load()
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "告警加载失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [load, tick]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(timer);
  }, [toast]);

  const checkNow = async () => {
    if (checking) return;
    setChecking(true);
    try {
      const data = await apiSend<CheckResult>("/api/alerts/check", "POST");
      setToast(`检查了 ${data.checked} 条，触发 ${data.fired.length} 条`);
      await load().catch(() => undefined);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "检查失败");
    } finally {
      setChecking(false);
    }
  };

  const toggle = async (rule: AlertRule) => {
    setBusyId(rule.id);
    try {
      await apiSend<AlertRule>(`/api/alerts/rules/${rule.id}`, "PATCH", { enabled: !rule.enabled });
      await load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusyId("");
    }
  };

  const test = async (rule: AlertRule) => {
    setBusyId(rule.id);
    setTestingId(rule.id);
    try {
      const res = await apiSend<{ ok: boolean; error: string }>(
        `/api/alerts/rules/${rule.id}/test`,
        "POST",
      );
      setToast(res.ok ? `「${rule.name}」的测试消息已发出` : `试发失败：${res.error || "未知原因"}`);
    } catch (e) {
      setToast(e instanceof Error ? e.message : "试发失败");
    } finally {
      setBusyId("");
      setTestingId("");
    }
  };

  const remove = async (rule: AlertRule) => {
    const ok = await confirmDialog({
      title: "删除规则",
      message: `删除规则「${rule.name}」？`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusyId(rule.id);
    try {
      await apiDelete(`/api/alerts/rules/${rule.id}`);
      setToast(`已删除「${rule.name}」`);
      await load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusyId("");
    }
  };

  if (loading && rules.length === 0) {
    return <p className="py-10 text-center text-[12.5px] text-tertiary">加载中…</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[12.5px] text-secondary">已配置 {rules.length} 条规则</span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={checking} onClick={() => void checkNow()}>
            {checking && <Loader2 size={13} className="mr-1.5 animate-spin" />}
            立即检查
          </Button>
          <Button onClick={() => setDialog({ rule: null })}>
            <Plus size={13} className="mr-1.5" />
            新建规则
          </Button>
        </div>
      </div>

      {error && (
        <Card className="space-y-2 p-4">
          <p className="text-[12.5px] text-danger">告警加载失败：{error}</p>
          <Button variant="outline" onClick={() => setTick((n) => n + 1)}>
            重试
          </Button>
        </Card>
      )}

      {toast && (
        <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12.5px] text-secondary">
          {toast}
        </p>
      )}

      <div className="space-y-2">
        {rules.map((rule) => (
          <Card key={rule.id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-medium text-primary">{rule.name}</span>
                  <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[12px] text-brand-deep dark:text-brand">
                    {rule.kind_label}
                  </span>
                  {!rule.enabled && (
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-tertiary">
                      已停用
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[12px] text-secondary">
                  {conditionText(rule, kinds)}
                  <span className="text-tertiary">
                    {" "}
                    · 窗口 {rule.window_hours} 小时 · 冷却 {rule.cooldown_minutes} 分钟 ·{" "}
                    {rule.channels.length} 个 webhook
                  </span>
                </p>
                <p className="mt-0.5 text-[12px] text-tertiary">
                  最近触发：{rule.last_fired_at ? timeText(rule.last_fired_at) : "从未触发"}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  type="button"
                  role="switch"
                  aria-checked={rule.enabled}
                  aria-label={rule.enabled ? "停用规则" : "启用规则"}
                  disabled={busyId === rule.id}
                  onClick={() => void toggle(rule)}
                  className={cn(
                    "relative mr-1 h-5 w-9 shrink-0 rounded-full transition-colors duration-150 disabled:opacity-50",
                    rule.enabled ? "bg-brand" : "bg-line-strong",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 h-4 w-4 rounded-full bg-surface-1 transition-all duration-150",
                      rule.enabled ? "left-[18px]" : "left-0.5",
                    )}
                  />
                </button>
                <Button
                  variant="ghost"
                  disabled={busyId === rule.id}
                  onClick={() => void test(rule)}
                >
                  {testingId === rule.id ? "试发中…" : "试发"}
                </Button>
                <Button variant="ghost" onClick={() => setDialog({ rule })}>
                  编辑
                </Button>
                <button
                  type="button"
                  disabled={busyId === rule.id}
                  onClick={() => void remove(rule)}
                  className="rounded-lg px-2.5 py-1.5 text-[12.5px] text-tertiary transition-colors duration-150 hover:bg-surface-3 hover:text-danger disabled:opacity-50"
                >
                  删除
                </button>
              </div>
            </div>
          </Card>
        ))}

        {rules.length === 0 && !error && (
          <p className="py-10 text-center text-[12.5px] text-tertiary">
            还没有告警规则。新建一条，出错或花费超线时会往 webhook 推消息。
          </p>
        )}
      </div>

      <Card className="p-5">
        <div className="flex items-baseline justify-between">
          <h3 className="text-[13px] font-medium">触发历史</h3>
          <span className="text-[12px] text-tertiary">最近 {events.length} 条</span>
        </div>
        <div className="mt-2">
          {events.map((event) => (
            <div
              key={event.id}
              className="flex items-start justify-between gap-3 border-t border-line py-2 first:border-t-0"
            >
              <div className="min-w-0">
                <p className="text-[12px] text-tertiary">
                  {timeText(event.fired_at)} · {event.rule_name}
                </p>
                <p
                  className="mt-0.5 truncate text-[12.5px] text-secondary"
                  title={event.message}
                >
                  {event.message.split("\n")[0] ?? event.message}
                </p>
              </div>
              {event.delivered ? (
                <span className="inline-flex shrink-0 items-center gap-1.5 text-[12px] text-secondary">
                  <span className="h-1.5 w-1.5 rounded-full bg-success" />
                  已送达
                </span>
              ) : (
                <span
                  className="inline-flex max-w-[300px] shrink-0 items-center gap-1.5 text-[12px] text-danger"
                  title={event.delivery_error ?? ""}
                >
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
                  <span className="truncate">
                    投递失败：{event.delivery_error || "未知原因"}
                  </span>
                </span>
              )}
            </div>
          ))}
          {events.length === 0 && (
            <p className="py-8 text-center text-[12.5px] text-tertiary">还没有触发记录</p>
          )}
        </div>
      </Card>

      {dialog && (
        <RuleDialog
          rule={dialog.rule}
          kinds={kinds}
          onClose={() => setDialog(null)}
          onSaved={(message) => {
            setDialog(null);
            setToast(message);
            void load().catch(() => undefined);
          }}
        />
      )}
    </div>
  );
}
