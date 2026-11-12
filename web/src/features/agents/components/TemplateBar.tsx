import { useEffect, useState } from "react";
import { BookmarkPlus, X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 任务模板：把"每周竞品简报"这类固定流程存成一条可复用起点。
 *
 * 这里只做"选模板 → 填进输入框"和"把当前任务存成模板"两件事。执行仍然交给
 * 智能体自主规划——模板一旦写死步骤，就退化成工作流引擎，和产品定位冲突。
 */

export interface TaskTemplate {
  id: string;
  name: string;
  description: string;
  prompt: string;
  expert_id: string;
  project: string;
  tool_ids: string[];
  kb_ids: string[];
  visibility: "private" | "workspace" | "shared";
  use_count: number;
  owner_id: string;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

/** 模板条：横向 chips，点击即填入任务。 */
export function TemplateBar({
  items,
  disabled,
  onPick,
}: {
  items: TaskTemplate[];
  disabled?: boolean;
  onPick: (tpl: TaskTemplate) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mb-3 flex flex-wrap items-center justify-center gap-1.5">
      <span className="mr-0.5 text-[12px] text-tertiary">模板</span>
      {items.slice(0, 6).map((tpl) => (
        <button
          key={tpl.id}
          disabled={disabled}
          onClick={() => onPick(tpl)}
          title={tpl.description || tpl.prompt}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-1",
            "text-[12px] text-secondary transition-colors duration-150",
            "hover:border-line-strong hover:text-primary disabled:opacity-40",
          )}
        >
          {tpl.name}
          {tpl.use_count > 0 && (
            <span className="text-[10px] tabular-nums text-tertiary">{tpl.use_count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/** 存为模板：只问名字（任务描述直接取当前输入框内容）。 */
export function SaveTemplateDialog({
  open,
  prompt,
  onClose,
  onSaved,
}: {
  open: boolean;
  prompt: string;
  onClose: () => void;
  onSaved: (tpl: TaskTemplate) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const save = async () => {
    if (!name.trim()) {
      setError("给模板起个名字");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          prompt,
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      onSaved((await res.json()) as TaskTemplate);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-[440px] rounded-xl border border-line bg-surface-1 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-primary">
            <BookmarkPlus size={14} strokeWidth={1.9} className="text-brand-ink "/>
            存为模板
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-tertiary transition-colors hover:bg-hover hover:text-primary"
          >
            <X size={14} strokeWidth={1.9} />
          </button>
        </div>

        <label className="mb-1 block text-[12px] text-tertiary">名称</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：每周竞品简报"
          autoFocus
          className="mb-3 w-full rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-[13px] text-primary outline-none focus:border-brand"
        />

        <label className="mb-1 block text-[12px] text-tertiary">说明（可选）</label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="这个模板解决什么问题"
          className="mb-3 w-full rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-[13px] text-primary outline-none focus:border-brand"
        />

        <p className="mb-3 line-clamp-3 rounded-md bg-surface-2 px-2.5 py-2 text-[12px] leading-relaxed text-tertiary">
          {prompt}
        </p>

        {error && <p className="mb-2 text-[12px] text-danger">{error}</p>}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-[12px] text-secondary transition-colors hover:bg-hover hover:text-primary"
          >
            取消
          </button>
          <button
            onClick={() => void save()}
            disabled={busy}
            className="rounded-md bg-brand px-3 py-1.5 text-[12px] font-medium text-brand-fg transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
