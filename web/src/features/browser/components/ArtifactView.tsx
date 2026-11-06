/**
 * 产物预览：html 直接渲染（服务端注入 base 与沙箱 CSP），图片/pdf 走原生渲染，
 * md 与代码支持就地编辑保存。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Download,
  ExternalLink,
  FileWarning,
  Loader2,
  Pencil,
  Presentation,
  RefreshCw,
  Save,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Markdown } from "@/features/chat/components/Markdown";
import * as api from "../api";

type Mode = "html" | "image" | "pdf" | "markdown" | "text" | "deck" | "binary";

const TEXT_EXT = new Set([
  "txt", "md", "markdown", "json", "yaml", "yml", "toml", "ini", "cfg", "csv", "tsv",
  "py", "js", "jsx", "ts", "tsx", "c", "h", "cpp", "hpp", "java", "go", "rs", "rb",
  "php", "sh", "bash", "bat", "ps1", "sql", "html", "htm", "css", "scss", "xml", "svg",
  "log", "env", "gitignore", "dockerfile", "makefile",
]);

function modeOf(path: string): Mode {
  const ext = path.includes(".") ? path.slice(path.lastIndexOf(".") + 1).toLowerCase() : "";
  if (["html", "htm"].includes(ext)) return "html";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (["md", "markdown"].includes(ext)) return "markdown";
  if (ext === "pptx") return "deck";
  if (TEXT_EXT.has(ext)) return "text";
  return "binary";
}

const KIND_LABEL: Record<Mode, string> = {
  html: "网页",
  image: "图片",
  pdf: "PDF",
  markdown: "Markdown",
  text: "文本",
  deck: "演示文稿",
  binary: "文件",
};

interface Props {
  project: string;
  path: string;
  ticket: string;
  onEditDeck: () => void;
  /** 外部写入（智能体刚改完文件）时递增，触发重新拉取 */
  refreshToken?: number;
}

export function ArtifactView({ project, path, ticket, onEditDeck, refreshToken = 0 }: Props) {
  const mode = useMemo(() => modeOf(path), [path]);
  const [reloadKey, setReloadKey] = useState(0);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(mode === "markdown");
  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  const editable = mode === "markdown" || mode === "text";

  const load = useCallback(() => {
    if (!editable) return;
    setLoading(true);
    setError("");
    api
      .readFile(project, path)
      .then((data) => {
        setContent(data.content);
        setOriginal(data.content);
        setLoading(false);
      })
      .catch((exc: Error) => {
        setError(exc.message);
        setLoading(false);
      });
  }, [project, path, editable]);

  useEffect(() => {
    setContent("");
    setOriginal("");
    load();
    setPreview(mode === "markdown");
  }, [load, mode, refreshToken]);

  const dirty = content !== original;

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await api.writeFile(project, path, content);
      setOriginal(content);
      setReloadKey((n) => n + 1);
      setError("");
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setSaving(false);
    }
  };

  // v 参数用于外部改写后绕开缓存；票据变了也会换 URL
  const url = `${api.previewUrl(project, path, ticket)}&v=${reloadKey}-${refreshToken}`;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
        <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-secondary">
          {KIND_LABEL[mode]}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-secondary" title={path}>
          {path}
        </span>
        {editable && mode === "markdown" && (
          <div className="flex items-center rounded-md border border-line p-0.5 text-[11.5px]">
            <button
              onClick={() => setPreview(true)}
              className={cn(
                "rounded px-2 py-0.5",
                preview ? "bg-surface-3 text-primary" : "text-tertiary hover:text-secondary",
              )}
            >
              预览
            </button>
            <button
              onClick={() => setPreview(false)}
              className={cn(
                "rounded px-2 py-0.5",
                !preview ? "bg-surface-3 text-primary" : "text-tertiary hover:text-secondary",
              )}
            >
              源码
            </button>
          </div>
        )}
        <button
          onClick={() => setReloadKey((n) => n + 1)}
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
          title="重新加载"
        >
          <RefreshCw size={13} />
        </button>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
          title="新标签打开"
        >
          <ExternalLink size={13} />
        </a>
        <a
          href={api.downloadUrl(project, path)}
          className="rounded-md p-1.5 text-secondary hover:bg-surface-3"
          title="下载"
        >
          <Download size={13} />
        </a>
        {editable && (
          <button
            onClick={save}
            disabled={!dirty || saving}
            className={cn(
              "flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] font-medium",
              dirty ? "bg-brand text-brand-fg" : "bg-surface-3 text-tertiary",
            )}
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            保存
          </button>
        )}
      </div>

      {error && (
        <div className="shrink-0 border-b border-danger/30 bg-danger/10 px-3 py-1.5 text-[11.5px] text-danger">
          {error}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        {mode === "html" && (
          <iframe
            key={reloadKey}
            src={url}
            title={path}
            className="h-full w-full border-0 bg-white"
            sandbox="allow-scripts allow-forms allow-popups allow-modals"
          />
        )}

        {mode === "image" && (
          <div className="flex h-full items-center justify-center overflow-auto bg-surface-2 p-6">
            <img
              key={reloadKey}
              src={url}
              alt={path}
              className="max-h-full max-w-full rounded bg-white shadow-[0_2px_12px_rgb(0_0_0/0.15)]"
            />
          </div>
        )}

        {mode === "pdf" && (
          <iframe key={reloadKey} src={url} title={path} className="h-full w-full border-0" />
        )}

        {mode === "deck" && (
          <div className="flex h-full flex-col items-center justify-center gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-soft text-brand">
              <Presentation size={24} strokeWidth={1.6} />
            </div>
            <div className="text-center">
              <p className="text-[14px] font-medium">这是可编辑的演示文稿</p>
              <p className="mt-1 text-[12px] text-tertiary">
                在编辑器里改文字、换版式与主题，保存后直接输出 pptx
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={onEditDeck}
                className="flex items-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-brand-fg"
              >
                <Pencil size={13} /> 打开编辑器
              </button>
              <a
                href={api.downloadUrl(project, path)}
                className="flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-[12.5px] text-secondary hover:bg-surface-3"
              >
                <Download size={13} /> 下载
              </a>
            </div>
          </div>
        )}

        {mode === "binary" && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <FileWarning size={22} className="text-tertiary" />
            <p className="text-[12.5px] text-secondary">这个类型无法预览</p>
            <a
              href={api.downloadUrl(project, path)}
              className="rounded-md border border-line px-3 py-1.5 text-[12.5px] text-secondary hover:bg-surface-3"
            >
              下载文件
            </a>
          </div>
        )}

        {editable && preview && mode === "markdown" && (
          <div className="h-full overflow-auto px-6 py-5">
            <Markdown content={content || "（空文件）"} />
          </div>
        )}

        {editable && !preview && (
          <textarea
            ref={editorRef}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            spellCheck={false}
            className="h-full w-full resize-none border-0 bg-surface-1 px-4 py-3 font-mono text-[12.5px] leading-relaxed text-primary outline-none"
            placeholder={loading ? "正在读取…" : ""}
          />
        )}
      </div>

      {editable && dirty && (
        <div className="flex shrink-0 items-center gap-2 border-t border-line bg-warning/10 px-3 py-1.5 text-[11.5px] text-warning">
          <span className="h-1.5 w-1.5 rounded-full bg-warning" />
          有未保存的修改
          <button
            onClick={() => setContent(original)}
            className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-warning/15"
          >
            <X size={11} /> 放弃
          </button>
        </div>
      )}
    </div>
  );
}
