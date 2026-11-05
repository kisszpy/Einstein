import { useEffect, useState } from "react";
import { FileText, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Skill, SkillFile } from "./types";

interface Props {
  skill: Skill | null;
  onClose: () => void;
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SkillFilesDialog({ skill, onClose }: Props) {
  const [files, setFiles] = useState<SkillFile[]>([]);
  const [active, setActive] = useState("");
  const [content, setContent] = useState("");
  const [isText, setIsText] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!skill) return;
    const list = skill.files ?? [];
    setFiles(list);
    // 默认打开 SKILL.md，其次第一个文件，省一次点击
    const preferred =
      list.find((f) => f.path.toLowerCase() === "skill.md")?.path ?? list[0]?.path ?? "";
    setActive(preferred);
    setContent("");
    setError("");
  }, [skill]);

  useEffect(() => {
    if (!skill || !active) return;
    let alive = true;
    setLoading(true);
    setError("");
    fetch(`/api/skills/${skill.id}/file?path=${encodeURIComponent(active)}`)
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "读取失败");
        if (!alive) return;
        setContent(data.content);
        setIsText(data.is_text);
      })
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : "读取失败"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [skill, active]);

  useEffect(() => {
    if (!skill) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [skill, onClose]);

  if (!skill) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/25 px-4 backdrop-blur-[2px]">
      <div className="flex h-[80vh] w-full max-w-[900px] flex-col overflow-hidden rounded-card border border-line bg-surface-1 shadow-float">
        <div className="flex items-start justify-between border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-[14px] font-medium">{skill.name} · 技能文件</h2>
            <a
              href={skill.source_url}
              target="_blank"
              rel="noreferrer"
              className="mt-0.5 block truncate text-[11.5px] text-tertiary hover:text-secondary"
            >
              {skill.repo}@{skill.commit.slice(0, 10)}
              {skill.subpath ? ` / ${skill.subpath}` : ""}
            </a>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-tertiary transition-colors duration-150 hover:bg-surface-3 hover:text-primary"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <aside className="w-64 shrink-0 overflow-y-auto border-r border-line bg-surface-2 p-2">
            {files.map((file) => (
              <button
                key={file.path}
                onClick={() => setActive(file.path)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] transition-colors duration-150",
                  active === file.path
                    ? "bg-brand-soft text-brand"
                    : "text-secondary hover:bg-surface-3 hover:text-primary",
                )}
              >
                <FileText size={13} className="shrink-0 opacity-70" />
                <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
                <span className="shrink-0 text-[10.5px] text-tertiary">{fileSize(file.size)}</span>
              </button>
            ))}
            {files.length === 0 && (
              <p className="px-2 py-3 text-[12px] text-tertiary">该技能没有附带资源文件</p>
            )}
          </aside>

          <div className="min-w-0 flex-1 overflow-auto p-4">
            {!active && <p className="text-[12.5px] text-tertiary">选择左侧文件查看内容</p>}
            {loading && (
              <p className="flex items-center gap-2 text-[12.5px] text-tertiary">
                <Loader2 size={13} className="animate-spin" />
                读取中…
              </p>
            )}
            {error && <p className="text-[12.5px] text-danger">{error}</p>}
            {!loading && !error && active && !isText && (
              <p className="text-[12.5px] text-tertiary">二进制文件，暂不支持预览</p>
            )}
            {!loading && !error && active && isText && (
              <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-[1.65] text-secondary">
                {content}
              </pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
