import { useCallback, useEffect, useState } from "react";
import { Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageBody, PageHeader } from "@/components/ui/page-header";
import {
  VisibilityBadge,
  VisibilitySelect,
  VISIBILITY_HINT,
  type Visibility,
} from "@/components/ui/visibility";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";

interface Expert {
  id: string;
  name: string;
  description: string;
  emoji: string;
  persona: string;
  skill_ids: string[];
  kb_ids: string[];
  tool_ids: string[];
  temperature: number;
  model_id?: string;
  enabled: boolean;
  greeting?: string;
  suggestions?: string[];
  status?: string;
  visibility?: string;
  owner_id?: string;
}

interface Skill {
  id: string;
  name: string;
  enabled: boolean;
}

interface ModelOption {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
}

interface KbInfo {
  id: string;
  name: string;
  document_count: number;
}

const TOOL_OPTIONS = [
  { id: "calculator", name: "计算器" },
  { id: "current_time", name: "当前时间" },
  { id: "text_stats", name: "文本统计" },
];

const EMPTY_FORM = {
  name: "",
  description: "",
  emoji: "🤖",
  persona: "",
  skill_ids: [] as string[],
  kb_ids: [] as string[],
  tool_ids: [] as string[],
  temperature: 0.7,
  model_id: "",
  greeting: "",
  suggestions: "" as string, // 逗号/换行分隔，保存时拆分
  visibility: "private" as Visibility,
};

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  testing: "测试中",
  live: "已上线",
};

function MultiSelect({
  label,
  options,
  selected,
  onToggle,
  emptyHint,
}: {
  label: string;
  options: { id: string; name: string; hint?: string }[];
  selected: string[];
  onToggle: (id: string) => void;
  emptyHint?: string;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-xs text-tertiary">{label}</span>
        {emptyHint && options.length === 0 && (
          <span className="text-xs text-tertiary">{emptyHint}</span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            onClick={() => onToggle(o.id)}
            className={cn(
              "rounded-md border px-2.5 py-1.5 text-xs transition-colors duration-150",
              selected.includes(o.id)
                ? "border-brand bg-brand-soft text-brand-ink "
                : "border-line text-secondary hover:border-line-strong",
            )}
          >
            {o.name}
            {o.hint && <span className="ml-1 text-tertiary">{o.hint}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ExpertsPage() {
  const [experts, setExperts] = useState<Expert[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [e, s, k] = await Promise.all([
      fetch("/api/experts").then((r) => r.json()),
      fetch("/api/skills").then((r) => r.json()),
      fetch("/api/kb").then((r) => r.json()),
    ]);
    setExperts(e);
    setSkills(s);
    setKbs(k);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 模型列表失败不影响专家表单：选项里只剩「跟随平台默认」
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch("/api/models");
        if (!res.ok) return;
        const data = (await res.json()) as { items?: ModelOption[] };
        if (alive && Array.isArray(data.items)) {
          setModels(data.items.filter((m) => m.kind === "chat" && m.enabled));
        }
      } catch {
        /* 后端没起或未登录时静默降级 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const startEdit = (e: Expert) => {
    setEditing(e.id);
    setForm({
      name: e.name,
      description: e.description,
      emoji: e.emoji,
      persona: e.persona,
      skill_ids: [...e.skill_ids],
      kb_ids: [...e.kb_ids],
      tool_ids: [...e.tool_ids],
      temperature: e.temperature,
      model_id: e.model_id ?? "",
      greeting: e.greeting ?? "",
      suggestions: (e.suggestions ?? []).join("、"),
      visibility: (e.visibility ?? "private") as Visibility,
    });
    setError(null);
  };

  const startNew = () => {
    setEditing("new");
    setForm(EMPTY_FORM);
    setError(null);
  };

  const save = async () => {
    if (!form.name.trim()) {
      setError("名称不能为空");
      return;
    }
    const payload = {
      ...form,
      model_id: form.model_id || null,
      suggestions: form.suggestions
        .split(/[、,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    };
    const url = editing === "new" ? "/api/experts" : `/api/experts/${editing}`;
    const res = await fetch(url, {
      method: editing === "new" ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      setError(`保存失败：HTTP ${res.status}`);
      return;
    }
    setEditing(null);
    await load();
  };

  const publish = async (e: Expert, status: string) => {
    const res = await fetch(`/api/experts/${e.id}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data.detail ?? "状态转移失败");
      return;
    }
    await load();
  };

  const remove = async (e: Expert) => {
    const ok = await confirmDialog({
      title: "删除专家",
      message: `删除专家「${e.name}」？`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    await fetch(`/api/experts/${e.id}`, { method: "DELETE" });
    await load();
  };

  const toggle = (key: "skill_ids" | "kb_ids" | "tool_ids", id: string) => {
    setForm((f) => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter((x) => x !== id) : [...f[key], id],
    }));
  };

  const skillName = (id: string) => skills.find((s) => s.id === id)?.name ?? id;
  const kbName = (id: string) => kbs.find((k) => k.id === id)?.name ?? id;

  return (
    <div className="h-full overflow-y-auto">
      <PageBody width="narrow" className="space-y-6">
        <PageHeader
          title="专家"
          description="专家 = 人设 + 挂载技能 + 挂载知识库 + 模型参数。对话页选择专家即生效。"
          actions={<Button onClick={startNew}>新建专家</Button>}
        />

        {/* 编辑表单 */}
        {editing && (
          <Card className="space-y-4 p-5">
            <h3 className="text-[13px] font-medium">
              {editing === "new" ? "新建专家" : "编辑专家"}
            </h3>
            <div className="grid grid-cols-[80px_1fr_1fr] gap-3">
              <input
                value={form.emoji}
                onChange={(e) => setForm({ ...form, emoji: e.target.value })}
                placeholder="图标"
                className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-center text-sm outline-none focus:border-brand"
              />
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="专家名称"
                className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="一句话简介"
                className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
            </div>
            <textarea
              value={form.persona}
              onChange={(e) => setForm({ ...form, persona: e.target.value })}
              placeholder="人设：这个专家是谁、以什么风格和方式回答（核心系统提示）"
              rows={4}
              className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <input
              value={form.greeting}
              onChange={(e) => setForm({ ...form, greeting: e.target.value })}
              placeholder="开场白：选中该专家后展示的欢迎语"
              className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <input
              value={form.suggestions}
              onChange={(e) => setForm({ ...form, suggestions: e.target.value })}
              placeholder="推荐问题：用顿号或换行分隔，对话页可一键发送"
              className="w-full rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <MultiSelect
              label="挂载技能"
              options={skills
                .filter((s) => s.enabled)
                .map((s) => ({ id: s.id, name: s.name }))}
              selected={form.skill_ids}
              onToggle={(id) => toggle("skill_ids", id)}
            />
            <MultiSelect
              label="挂载知识库"
              options={kbs.map((k) => ({
                id: k.id,
                name: k.name,
                hint: `(${k.document_count})`,
              }))}
              selected={form.kb_ids}
              onToggle={(id) => toggle("kb_ids", id)}
              emptyHint="还没有知识库，可先到「知识库」页创建"
            />
            <MultiSelect
              label="附加工具"
              options={TOOL_OPTIONS}
              selected={form.tool_ids}
              onToggle={(id) => toggle("tool_ids", id)}
            />
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs text-tertiary">温度</span>
              <input
                type="range"
                min={0}
                max={1.5}
                step={0.1}
                value={form.temperature}
                onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
                className="w-48 accent-[var(--brand)]"
              />
              <span className="text-xs text-secondary">{form.temperature.toFixed(1)}</span>
              <span className="ml-4 text-xs text-tertiary">模型</span>
              <select
                value={form.model_id}
                onChange={(e) => setForm({ ...form, model_id: e.target.value })}
                className="h-8 max-w-[200px] rounded-lg border border-line bg-surface-1 px-2 text-xs text-primary outline-none focus:border-brand"
              >
                <option value="">跟随平台默认</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
              <span className="ml-4 text-xs text-tertiary">可见范围</span>
              <VisibilitySelect
                value={form.visibility}
                onChange={(v) => setForm({ ...form, visibility: v })}
              />
              <span className="text-[12px] text-tertiary">{VISIBILITY_HINT[form.visibility]}</span>
            </div>
            {error && <p className="text-xs text-danger">{error}</p>}
            <div className="flex gap-2">
              <Button onClick={() => void save()}>保存</Button>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                取消
              </Button>
            </div>
          </Card>
        )}

        {/* 专家列表 */}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {experts.map((e) => (
            <Card key={e.id} className="flex flex-col p-4">
              <div className="flex items-start gap-3">
                <span className="text-2xl leading-none">{e.emoji}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-primary">{e.name}</span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[12px]",
                          e.status === "live"
                            ? "bg-brand-soft text-brand-ink "
                            : e.status === "testing"
                              ? "bg-warning/15 text-warning"
                              : "bg-surface-3 text-tertiary",
                        )}
                      >
                        {STATUS_LABEL[e.status ?? "draft"] ?? e.status}
                      </span>
                      <VisibilityBadge value={e.visibility} />
                    </div>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-secondary">{e.description}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {e.skill_ids.map((id) => (
                        <span
                          key={id}
                          className="rounded-md bg-brand-soft px-1.5 py-0.5 text-[11.5px] text-brand-ink "
                        >
                          {skillName(id)}
                        </span>
                      ))}
                      {e.kb_ids.map((id) => (
                        <span
                          key={id}
                          className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[11.5px] text-secondary"
                        >
                          📚 {kbName(id)}
                        </span>
                      ))}
                      {e.tool_ids.map((t) => (
                        <span
                          key={t}
                          className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[11.5px] text-secondary"
                        >
                          🔧 {TOOL_OPTIONS.find((o) => o.id === t)?.name ?? t}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-2.5">
                  {/* 发布流：draft → testing → live，可回退 */}
                  <div className="flex items-center gap-1">
                    {(e.status ?? "draft") === "draft" && (
                      <Button variant="ghost" onClick={() => void publish(e, "testing")}>
                        转测试
                      </Button>
                    )}
                    {(e.status ?? "draft") === "testing" && (
                      <>
                        <Button variant="ghost" onClick={() => void publish(e, "live")}>
                          上线
                        </Button>
                        <Button variant="ghost" onClick={() => void publish(e, "draft")}>
                          退回草稿
                        </Button>
                      </>
                    )}
                    {e.status === "live" && (
                      <Button variant="ghost" onClick={() => void publish(e, "testing")}>
                        下架回测试
                      </Button>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" onClick={() => startEdit(e)}>
                      编辑
                    </Button>
                    <Button variant="ghost" onClick={() => void remove(e)}>
                      删除
                    </Button>
                  </div>
                </div>
            </Card>
          ))}
          {experts.length === 0 && (
            <div className="md:col-span-2">
              <EmptyState
                icon={Users}
                title="还没有专家"
                description="点击右上角「新建专家」，配好人设、技能和知识库后即可在对话页选用。"
                action={<Button onClick={startNew}>新建专家</Button>}
              />
            </div>
          )}
        </div>
      </PageBody>
    </div>
  );
}
