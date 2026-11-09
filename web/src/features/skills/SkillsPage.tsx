import { useCallback, useEffect, useRef, useState } from "react";
import {
  Download,
  FlaskConical,
  FolderOpen,
  GitBranch,
  History,
  Loader2,
  Plus,
  RefreshCw,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  VisibilityBadge,
  VisibilitySelect,
  VISIBILITY_HINT,
  type Visibility,
} from "@/components/ui/visibility";
import { InstallDialog } from "./InstallDialog";
import { MarketPanel } from "./MarketPanel";
import { SkillFilesDialog } from "./SkillFilesDialog";
import type { Skill, SkillVersion } from "./types";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";

const TOOL_OPTIONS = [
  { id: "calculator", name: "计算器" },
  { id: "current_time", name: "当前时间" },
  { id: "text_stats", name: "文本统计" },
];

const EMPTY_FORM = {
  name: "",
  description: "",
  system_prompt: "",
  tools: [] as string[],
  visibility: "private" as Visibility,
};

function shortDate(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return date.toLocaleDateString("zh-CN");
}

export function SkillsPage() {
  const [tab, setTab] = useState<"installed" | "market">("installed");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [busyId, setBusyId] = useState("");
  const [installOpen, setInstallOpen] = useState(false);
  const [installUrl, setInstallUrl] = useState("");
  const [filesSkill, setFilesSkill] = useState<Skill | null>(null);
  // ---- S2.6：版本管理 / 导入导出 / 沙箱试运行 ----
  const [versionSkill, setVersionSkill] = useState<Skill | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [trySkill, setTrySkill] = useState<Skill | null>(null);
  const [tryMessage, setTryMessage] = useState("");
  const [tryReply, setTryReply] = useState("");
  const [tryBusy, setTryBusy] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/skills");
    if (res.ok) setSkills(await res.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(timer);
  }, [toast]);

  const startEdit = (s: Skill) => {
    setEditing(s.id);
    setForm({
      name: s.name,
      description: s.description,
      system_prompt: s.system_prompt,
      tools: [...s.tools],
      visibility: (s.visibility ?? "private") as Visibility,
    });
    setError(null);
  };

  const startNew = () => {
    setTab("installed");
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
      const data = await res.json().catch(() => ({}));
      setError(data.detail || `保存失败：HTTP ${res.status}`);
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
    const hint = s.source === "github" ? "同时删除已下载的技能文件" : "";
    const ok = await confirmDialog({
      title: "删除技能",
      message: `删除技能「${s.name}」？${hint}`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusyId(s.id);
    await fetch(`/api/skills/${s.id}`, { method: "DELETE" });
    setBusyId("");
    await load();
  };

  const updateFromSource = async (s: Skill) => {
    setBusyId(s.id);
    try {
      const res = await fetch(`/api/skills/${s.id}/update`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setToast(data.detail || "更新失败");
        return;
      }
      setToast(
        data.changed
          ? `「${s.name}」已更新到 ${data.skill.commit.slice(0, 10)}`
          : `「${s.name}」已是最新版本（${data.skill.commit.slice(0, 10)}）`,
      );
      await load();
    } finally {
      setBusyId("");
    }
  };

  const toggleTool = (id: string) => {
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(id) ? f.tools.filter((t) => t !== id) : [...f.tools, id],
    }));
  };

  // ---- S2.6 ----
  const openVersions = async (s: Skill) => {
    setVersionSkill(s);
    const res = await fetch(`/api/skills/${s.id}/versions`);
    if (res.ok) {
      const data = await res.json();
      setVersions(data.versions ?? []);
    }
  };

  const rollback = async (version: string) => {
    if (!versionSkill) return;
    const res = await fetch(`/api/skills/${versionSkill.id}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    });
    if (res.ok) {
      setToast(`已回滚到 ${version}`);
      await load();
      await openVersions(versionSkill);
    } else {
      setToast("回滚失败");
    }
  };

  const runTry = async () => {
    if (!trySkill || !tryMessage.trim()) return;
    setTryBusy(true);
    setTryReply("");
    try {
      const res = await fetch(`/api/skills/${trySkill.id}/try`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: tryMessage }),
      });
      const data = await res.json();
      setTryReply(res.ok ? data.reply : (data.detail ?? "试运行失败"));
    } catch (e) {
      setTryReply(`试运行失败：${(e as Error).message}`);
    } finally {
      setTryBusy(false);
    }
  };

  const importZip = async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/skills/import", { method: "POST", body });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      setToast(`已导入技能「${data.name}」(${data.version})`);
      setTab("installed");
      await load();
    } else {
      setToast(data.detail ?? "导入失败");
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[860px] space-y-5 px-8 py-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-medium">技能</h1>
            <p className="mt-1 text-[13px] text-secondary">
              可复用的能力包。既可以是本地编写的提示词 + 工具声明，也可以直接从 GitHub 安装
              Agent Skills 技能包（SKILL.md + 资源文件）。
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <input
              ref={importInputRef}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void importZip(file);
                e.target.value = "";
              }}
            />
            <Button variant="outline" onClick={() => importInputRef.current?.click()}>
              <Upload size={13} className="mr-1.5" />
              导入 zip
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setInstallUrl("");
                setInstallOpen(true);
              }}
            >
              <Download size={13} className="mr-1.5" />
              从 GitHub 安装
            </Button>
            <Button onClick={startNew}>
              <Plus size={13} className="mr-1.5" />
              新建技能
            </Button>
          </div>
        </div>

        <div className="flex w-fit gap-1 rounded-lg border border-line bg-surface-2 p-1">
          {(
            [
              { id: "installed", label: `已安装 ${skills.length}` },
              { id: "market", label: "技能市场" },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={cn(
                "rounded-md px-3 py-1.5 text-[12.5px] transition-colors duration-150",
                tab === item.id
                  ? "bg-surface-1 text-primary shadow-card"
                  : "text-secondary hover:text-primary",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>

        {toast && (
          <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12.5px] text-secondary">
            {toast}
          </p>
        )}

        {tab === "market" && (
          <MarketPanel
            onInstall={(url) => {
              setInstallUrl(url);
              setInstallOpen(true);
            }}
          />
        )}

        {tab === "installed" && (
          <>
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
                  rows={6}
                  className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 font-mono text-[12.5px] outline-none focus:border-brand"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-tertiary">依赖工具：</span>
                  {TOOL_OPTIONS.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => toggleTool(t.id)}
                      className={cn(
                        "rounded-md border px-2 py-1 text-xs transition-colors duration-150",
                        form.tools.includes(t.id)
                          ? "border-brand bg-brand-soft text-brand-deep dark:text-brand"
                          : "border-line text-secondary hover:border-line-strong",
                      )}
                    >
                      {t.name}
                    </button>
                  ))}
                  <span className="ml-4 text-xs text-tertiary">可见范围</span>
                  <VisibilitySelect
                    value={form.visibility}
                    onChange={(v) => setForm({ ...form, visibility: v })}
                  />
                  <span className="text-[12px] text-tertiary">{VISIBILITY_HINT[form.visibility]}</span>
                </div>
                {editing !== "new" && skills.find((s) => s.id === editing)?.source === "github" && (
                  <p className="text-[12px] text-tertiary">
                    该技能来自 GitHub：手动修改后，再次「更新」会用仓库内容覆盖名称、描述与提示词。
                  </p>
                )}
                {error && <p className="text-xs text-danger">{error}</p>}
                <div className="flex gap-2">
                  <Button onClick={() => void save()}>保存</Button>
                  <Button variant="ghost" onClick={() => setEditing(null)}>
                    取消
                  </Button>
                </div>
              </Card>
            )}

            <div className="space-y-2">
              {skills.map((s) => (
                <Card key={s.id} className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-medium text-primary">{s.name}</span>
                        {s.version && (
                          <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[12px] text-secondary">
                            v{s.version}
                          </span>
                        )}
                        {s.builtin && (
                          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-tertiary">
                            内置
                          </span>
                        )}
                        {s.source === "github" && (
                          <span className="flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-secondary">
                            <GitBranch size={10} />
                            GitHub
                          </span>
                        )}
                        {!s.enabled && (
                          <span className="rounded bg-danger/10 px-1.5 py-0.5 text-[12px] text-danger">
                            已停用
                          </span>
                        )}
                        <VisibilityBadge value={s.visibility} />
                      </div>
                      <p className="mt-1 text-[12px] text-secondary">{s.description}</p>

                      {s.source === "github" ? (
                        <a
                          href={s.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[12px] text-tertiary transition-colors duration-150 hover:text-secondary"
                        >
                          <span>{s.repo}</span>
                          <span className="rounded bg-surface-3 px-1 py-0.5">{s.commit.slice(0, 8)}</span>
                          <span>{s.files.length} 文件</span>
                          {s.author && <span>· {s.author}</span>}
                          <span>· 安装于 {shortDate(s.installed_at)}</span>
                        </a>
                      ) : (
                        s.system_prompt && (
                          <p className="mt-1 line-clamp-2 text-[12px] text-tertiary">
                            {s.system_prompt}
                          </p>
                        )
                      )}

                      {s.tools.length > 0 && (
                        <div className="mt-1.5 flex gap-1">
                          {s.tools.map((t) => (
                            <span
                              key={t}
                              className="rounded bg-surface-3 px-1.5 py-0.5 text-[12px] text-secondary"
                            >
                              {TOOL_OPTIONS.find((o) => o.id === t)?.name ?? t}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        variant="ghost"
                        title="沙箱试运行：用该技能提示词回答一条样例消息"
                        onClick={() => {
                          setTrySkill(s);
                          setTryMessage("");
                          setTryReply("");
                        }}
                      >
                        <FlaskConical size={12} />
                        <span className="ml-1">试运行</span>
                      </Button>
                      <Button variant="ghost" onClick={() => void openVersions(s)}>
                        <History size={12} />
                        <span className="ml-1">版本</span>
                      </Button>
                      <a
                        href={`/api/skills/${s.id}/export`}
                        className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-[12.5px] text-secondary transition-colors duration-150 hover:bg-hover hover:text-primary"
                        title="导出为 zip（含 skill.json 元数据与全部文件）"
                      >
                        <Download size={12} />
                        导出
                      </a>
                      {s.source === "github" && (
                        <>
                          <Button
                            variant="ghost"
                            disabled={busyId === s.id}
                            onClick={() => void updateFromSource(s)}
                          >
                            {busyId === s.id ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <RefreshCw size={12} />
                            )}
                            <span className="ml-1">更新</span>
                          </Button>
                          <Button variant="ghost" onClick={() => setFilesSkill(s)}>
                            <FolderOpen size={12} />
                            <span className="ml-1">文件</span>
                          </Button>
                        </>
                      )}
                      <Button variant="ghost" onClick={() => void toggleEnabled(s)}>
                        {s.enabled ? "停用" : "启用"}
                      </Button>
                      <Button variant="ghost" onClick={() => startEdit(s)}>
                        编辑
                      </Button>
                      {!s.builtin && (
                        <Button
                          variant="ghost"
                          disabled={busyId === s.id}
                          onClick={() => void remove(s)}
                        >
                          删除
                        </Button>
                      )}
                    </div>
                  </div>
                </Card>
              ))}

              {skills.length === 0 && (
                <p className="py-10 text-center text-[12.5px] text-tertiary">
                  还没有技能，先新建一个，或从市场安装现成的技能包。
                </p>
              )}
            </div>
          </>
        )}

        <InstallDialog
          open={installOpen}
          initialUrl={installUrl}
          onClose={() => setInstallOpen(false)}
          onInstalled={(count) => {
            setTab("installed");
            setToast(`已安装 ${count} 个技能，可在专家中挂载使用`);
            void load();
          }}
        />

        <SkillFilesDialog skill={filesSkill} onClose={() => setFilesSkill(null)} />

        {/* 版本历史与回滚 */}
        {versionSkill && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
            onClick={() => setVersionSkill(null)}
          >
            <Card
              className="max-h-[70vh] w-full max-w-[520px] overflow-y-auto p-5"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-[13px] font-medium">
                版本历史 · {versionSkill.name}
                <span className="ml-2 font-mono text-[12px] text-tertiary">
                  当前 v{versionSkill.version}
                </span>
              </h3>
              <ul className="mt-3 space-y-2">
                {versions.map((v) => (
                  <li
                    key={v.version}
                    className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="font-mono text-[12px] text-primary">
                        v{v.version}
                        {v.note && <span className="ml-2 text-tertiary">{v.note}</span>}
                      </p>
                      <p className="truncate text-[12px] text-tertiary">
                        {v.saved_at.replace("T", " ")} · {(v.system_prompt ?? "").slice(0, 40)}
                      </p>
                    </div>
                    <Button variant="ghost" onClick={() => void rollback(v.version)}>
                      回滚到此版
                    </Button>
                  </li>
                ))}
                {versions.length === 0 && (
                  <p className="py-6 text-center text-[12px] text-tertiary">
                    还没有历史版本；编辑内容后会自动生成快照。
                  </p>
                )}
              </ul>
            </Card>
          </div>
        )}

        {/* 沙箱试运行 */}
        {trySkill && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
            onClick={() => setTrySkill(null)}
          >
            <Card
              className="w-full max-w-[520px] space-y-3 p-5"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-[13px] font-medium">
                沙箱试运行 · {trySkill.name}
                <span className="ml-2 font-mono text-[12px] text-tertiary">
                  v{trySkill.version}
                </span>
              </h3>
              <textarea
                value={tryMessage}
                onChange={(e) => setTryMessage(e.target.value)}
                placeholder="输入一条样例消息，看看这个技能会怎样回答（限 400 token）"
                rows={3}
                className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm outline-none focus:border-brand"
              />
              <div className="flex items-center gap-2">
                <Button disabled={tryBusy || !tryMessage.trim()} onClick={() => void runTry()}>
                  {tryBusy ? <Loader2 size={12} className="animate-spin" /> : null}
                  <span className="ml-1">{tryBusy ? "运行中…" : "运行"}</span>
                </Button>
                <Button variant="ghost" onClick={() => setTrySkill(null)}>
                  关闭
                </Button>
              </div>
              {tryReply && (
                <div className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-2 p-3 text-[12.5px] leading-relaxed text-secondary">
                  {tryReply}
                </div>
              )}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
