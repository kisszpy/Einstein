import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Skill {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  tools: string[];
  builtin: boolean;
  enabled: boolean;
}

const TOOL_OPTIONS = [
  { id: "calculator", name: "计算器" },
  { id: "current_time", name: "当前时间" },
  { id: "text_stats", name: "文本统计" },
];

const EMPTY_FORM = { name: "", description: "", system_prompt: "", tools: [] as string[] };

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/skills");
    setSkills(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = (s: Skill) => {
    setEditing(s.id);
    setForm({
      name: s.name,
      description: s.description,
      system_prompt: s.system_prompt,
      tools: [...s.tools],
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
    const url = editing === "new" ? "/api/skills" : `/api/skills/${editing}`;
    const res = await fetch(url, {
      method: editing === "new" ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    if (!res.ok) {
      setError(`保存失败：HTTP ${res.status}`);
      return;
    }
    setEditing(null);
    await load();
  };

  const toggleEnabled = async (s: Skill) => {
    await fetch(`/api/skills/${s.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !s.enabled }),
    });
    await load();
  };

  const remove = async (s: Skill) => {
    if (!window.confirm(`删除技能「${s.name}」？`)) return;
    await fetch(`/api/skills/${s.id}`, { method: "DELETE" });
    await load();
  };

  const toggleTool = (id: string) => {
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(id) ? f.tools.filter((t) => t !== id) : [...f.tools, id],
    }));
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[860px] space-y-6 px-8 py-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-lg font-medium">技能</h1>
            <p className="mt-1 text-[13px] text-secondary">
              可复用的能力包：提示词模板 + 工具声明。专家挂载技能即获得对应能力。
            </p>
          </div>
          <Button onClick={startNew}>新建技能</Button>
        </div>

        {/* 编辑表单 */}
        {editing && (
          <Card className="space-y-3 p-5">
            <h3 className="text-[13px] font-medium">
              {editing === "new" ? "新建技能" : "编辑技能"}
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="技能名称"
                className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="一句话描述"
                className="rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
            </div>
            <textarea
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              placeholder="系统提示词：定义技能被挂载时注入的行为约束（留空则不注入）"
              rows={5}
              className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
            />
            <div className="flex items-center gap-2">
              <span className="text-xs text-tertiary">依赖工具：</span>
              {TOOL_OPTIONS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => toggleTool(t.id)}
                  className={cn(
                    "rounded-md border px-2 py-1 text-xs transition-colors duration-150",
                    form.tools.includes(t.id)
                      ? "border-brand bg-brand-soft text-brand"
                      : "border-line text-secondary hover:border-line-strong",
                  )}
                >
                  {t.name}
                </button>
              ))}
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

        {/* 技能列表 */}
        <div className="space-y-2">
          {skills.map((s) => (
            <Card key={s.id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-primary">{s.name}</span>
                    {s.builtin && (
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-tertiary">
                        内置
                      </span>
                    )}
                    {!s.enabled && (
                      <span className="rounded bg-danger/10 px-1.5 py-0.5 text-xs text-danger">
                        已停用
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-secondary">{s.description}</p>
                  {s.system_prompt && (
                    <p className="mt-1 line-clamp-2 text-xs text-tertiary">{s.system_prompt}</p>
                  )}
                  {s.tools.length > 0 && (
                    <div className="mt-1.5 flex gap-1">
                      {s.tools.map((t) => (
                        <span
                          key={t}
                          className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-secondary"
                        >
                          {TOOL_OPTIONS.find((o) => o.id === t)?.name ?? t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button variant="ghost" onClick={() => void toggleEnabled(s)}>
                    {s.enabled ? "停用" : "启用"}
                  </Button>
                  <Button variant="ghost" onClick={() => startEdit(s)}>
                    编辑
                  </Button>
                  {!s.builtin && (
                    <Button variant="ghost" onClick={() => void remove(s)}>
                      删除
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
