import { useCallback, useEffect, useState } from "react";
import { Check, GitBranch, Loader2, Star, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Preview } from "./types";

interface Props {
  open: boolean;
  initialUrl?: string;
  onClose: () => void;
  onInstalled: (count: number) => void;
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function InstallDialog({ open, initialUrl = "", onClose, onInstalled }: Props) {
  const [url, setUrl] = useState(initialUrl);
  const [ref, setRef] = useState("");
  const [showRef, setShowRef] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState("");

  const runPreview = useCallback(
    async (target: string, branch: string) => {
      if (!target.trim()) {
        setError("请填写 GitHub 仓库地址");
        return;
      }
      setLoading(true);
      setError("");
      setPreview(null);
      try {
        const res = await fetch("/api/skills/install/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: target.trim(), ref: branch.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "预览失败");
        setPreview(data);
        const preset = data.selected?.path ?? (data.candidates.length === 1 ? data.candidates[0].path : "");
        setPicked(preset ? [preset] : []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "预览失败");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!open) return;
    setUrl(initialUrl);
    setRef("");
    setPreview(null);
    setPicked([]);
    setError("");
    if (initialUrl) void runPreview(initialUrl, "");
  }, [open, initialUrl, runPreview]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !installing) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, installing, onClose]);

  if (!open) return null;

  const toggle = (path: string) =>
    setPicked((prev) => (prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path]));

  const install = async () => {
    if (!preview || picked.length === 0) return;
    setInstalling(true);
    setError("");
    let done = 0;
    try {
      for (const path of picked) {
        const res = await fetch("/api/skills/install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, ref, path }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(`${path || "根目录"}：${data.detail || "安装失败"}`);
        done += 1;
      }
      onInstalled(done);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "安装失败");
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/25 px-4 backdrop-blur-[2px]">
      <div className="flex max-h-[86vh] w-full max-w-[680px] flex-col overflow-hidden rounded-card border border-line bg-surface-1 shadow-float">
        <div className="flex items-start justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="flex items-center gap-2 text-[14px] font-medium">
              <GitBranch size={15} className="text-tertiary" />
              从 GitHub 安装技能
            </h2>
            <p className="mt-1 text-[12px] text-tertiary">
              支持仓库根地址或 /tree/&lt;分支&gt;/&lt;目录&gt;；按 SKILL.md 识别技能包，只读取文件、不执行代码。
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={installing}
            className="rounded-md p-1 text-tertiary transition-colors duration-150 hover:bg-surface-3 hover:text-primary disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <label htmlFor="repo-url" className="mb-1.5 block text-[12.5px] text-secondary">
            仓库地址
          </label>
          <div className="flex gap-2">
            <input
              id="repo-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void runPreview(url, ref)}
              placeholder="https://github.com/anthropics/skills"
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface-1 px-2.5 text-[13px] outline-none transition-colors duration-150 placeholder:text-tertiary focus:border-brand"
            />
            <Button onClick={() => void runPreview(url, ref)} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : "预览"}
            </Button>
          </div>

          <button
            onClick={() => setShowRef((v) => !v)}
            className="mt-2 text-[11.5px] text-tertiary transition-colors duration-150 hover:text-secondary"
          >
            {showRef ? "收起高级选项" : "指定分支 / 标签（默认跟随默认分支）"}
          </button>
          {showRef && (
            <input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="main / v1.2.0"
              className="mt-2 h-8 w-48 rounded-lg border border-line bg-surface-1 px-2.5 text-[12.5px] outline-none focus:border-brand"
            />
          )}

          {error && (
            <p className="mt-3 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[12.5px] text-danger">
              {error}
            </p>
          )}

          {preview && (
            <div className="mt-4 space-y-3">
              <div className="rounded-lg border border-line bg-surface-2 px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <a
                    href={preview.repo.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[13px] font-medium text-primary hover:text-brand"
                  >
                    {preview.repo.repo}
                  </a>
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px] text-secondary">
                    {preview.repo.ref} @ {preview.repo.commit_short}
                  </span>
                  <span className="flex items-center gap-1 text-[11.5px] text-tertiary">
                    <Star size={11} />
                    {preview.repo.stars.toLocaleString()}
                  </span>
                  {preview.repo.license && (
                    <span className="text-[11.5px] text-tertiary">{preview.repo.license}</span>
                  )}
                </div>
                {preview.repo.description && (
                  <p className="mt-1 line-clamp-2 text-[12px] text-tertiary">{preview.repo.description}</p>
                )}
              </div>

              <div className="flex items-center justify-between">
                <p className="text-[12.5px] text-secondary">
                  识别到 {preview.candidates.length} 个技能
                  {preview.candidates.length > 1 && "，可多选"}
                </p>
                {preview.candidates.length > 1 && (
                  <button
                    onClick={() =>
                      setPicked(
                        picked.length === preview.candidates.length
                          ? []
                          : preview.candidates.map((c) => c.path),
                      )
                    }
                    className="text-[11.5px] text-brand transition-opacity duration-150 hover:opacity-80"
                  >
                    {picked.length === preview.candidates.length ? "取消全选" : "全选"}
                  </button>
                )}
              </div>

              <div className="max-h-[36vh] space-y-1 overflow-y-auto pr-1">
                {preview.candidates.map((c) => {
                  const on = picked.includes(c.path);
                  return (
                    <button
                      key={c.path || "root"}
                      onClick={() => toggle(c.path)}
                      className={cn(
                        "flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors duration-150",
                        on
                          ? "border-brand/40 bg-brand-soft"
                          : "border-line hover:border-line-strong hover:bg-surface-2",
                      )}
                    >
                      <span
                        className={cn(
                          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors duration-150",
                          on ? "border-brand bg-brand text-brand-fg" : "border-line-strong",
                        )}
                      >
                        {on && <Check size={11} strokeWidth={3} />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[13px] font-medium text-primary">{c.name}</span>
                          <span className="shrink-0 text-[11px] text-tertiary">
                            {c.file_count} 文件 · {fileSize(c.size_bytes)}
                          </span>
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[11px] text-tertiary">
                          {c.path || "仓库根目录"}
                        </span>
                        {c.description && (
                          <span className="mt-0.5 line-clamp-2 block text-[11.5px] leading-4 text-secondary">
                            {c.description}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-line px-5 py-3">
          <p className="text-[11.5px] text-tertiary">
            {preview ? `已选 ${picked.length} 个` : "先预览，再安装"}
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={installing}>
              取消
            </Button>
            <Button onClick={() => void install()} disabled={installing || picked.length === 0}>
              {installing ? "正在安装…" : "安装"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
