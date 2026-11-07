import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 与服务端 parsers.SUPPORTED_EXTENSIONS 保持一致 */
const SUPPORTED = [
  ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".html", ".htm",
  ".txt", ".md", ".markdown",
];
/** 后端也会再过滤一遍，这里提前拦住可以少传垃圾文件 */
const SKIP_DIRS = new Set([
  "__MACOSX", ".git", ".svn", "node_modules", ".venv", "venv",
  "__pycache__", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
]);
const MAX_FILE_BYTES = 50 * 1024 * 1024;

interface Picked {
  file: File;
  path: string; // 相对路径，作为文档名
}

interface Skipped {
  name: string;
  reason: string;
}

/** 失败条目由后端返回 {name, error}，与「跳过」的原因字段不同 */
interface FailedItem {
  name: string;
  error: string;
}

interface JobProgress {
  stage?: string;
  percent?: number;
  done?: number;
  total?: number;
  current?: string;
  ok?: number;
  failed?: number;
  failed_items?: FailedItem[];
}

interface JobView {
  id: string;
  status: string;
  progress?: JobProgress;
  result?: {
    total?: number;
    ok?: number;
    failed?: number;
    deduped?: number;
    chunks?: number;
    failed_items?: FailedItem[];
  };
  error?: string;
}

interface Props {
  kbId: string;
  /** 导入结束（成功或失败）后通知父级刷新文档列表 */
  onFinished: () => void;
}

/** 批量接口返回：大批量会拆成多个任务并行跑（job_ids） */
interface BatchResponse {
  job_id: string;
  job_ids?: string[];
  jobs?: Array<{ job_id: string; files: number }>;
  shards?: number;
  files?: number;
  skipped?: Skipped[];
}

const ext = (name: string) => {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
};

/** 相对路径 → 该不该跳过。返回原因字符串表示跳过。 */
function skipReason(path: string, size: number): string {
  const parts = path.split("/");
  const dirs = parts.slice(0, -1);
  if (dirs.some((d) => SKIP_DIRS.has(d) || d.startsWith("."))) return "系统/依赖目录";
  const leaf = parts[parts.length - 1] ?? "";
  if (!leaf || leaf.startsWith(".")) return "隐藏文件";
  if (!SUPPORTED.includes(ext(leaf))) return "不支持的格式";
  if (size > MAX_FILE_BYTES) return "超过 50MB";
  return "";
}

function toWebkitPath(file: File): string {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return (rel && rel.trim()) || file.name;
}

/** 把 FileList 拆成「可导入」与「跳过」两堆 */
function classify(files: Array<{ file: File; path: string }>) {
  const picked: Picked[] = [];
  const skipped: Skipped[] = [];
  for (const item of files) {
    const reason = skipReason(item.path, item.file.size);
    if (reason) {
      skipped.push({ name: item.path, reason });
      continue;
    }
    picked.push(item);
  }
  return { picked, skipped };
}

/** 递归展开拖入的目录项；readEntries 每次最多 100 项，必须读到空为止 */
async function walkEntry(entry: FileSystemEntry, parent: string, out: Picked[]): Promise<void> {
  const path = parent ? `${parent}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push({ file, path });
    return;
  }
  if (!entry.isDirectory) return;
  if (SKIP_DIRS.has(entry.name) || entry.name.startsWith(".")) return;
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (batch.length === 0) break;
    for (const child of batch) await walkEntry(child, path, out);
  }
}

const fmtSize = (bytes: number) =>
  bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;

export function BatchImport({ kbId, onFinished }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);
  const zipInput = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<Picked[]>([]);
  const [skipped, setSkipped] = useState<Skipped[]>([]);
  const [dragging, setDragging] = useState(false);
  const [jobIds, setJobIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dirPath, setDirPath] = useState("");
  const [dirOpen, setDirOpen] = useState(false);
  const [showSkipped, setShowSkipped] = useState(false);

  // webkitdirectory 不是标准 React 属性，用 ref 显式设置；顺带关掉目录选择框里"选文件"的默认行为
  useEffect(() => {
    dirInput.current?.setAttribute("webkitdirectory", "");
    dirInput.current?.setAttribute("directory", "");
  }, []);

  const running = jobIds.length > 0;
  const totalBytes = picked.reduce((sum, p) => sum + p.file.size, 0);

  const take = useCallback((items: Array<{ file: File; path: string }>, extra: Skipped[] = []) => {
    const { picked: ok, skipped: bad } = classify(items);
    setPicked((prev) => [...prev, ...ok]);
    setSkipped((prev) => [...prev, ...extra, ...bad]);
    setError(null);
    setJobs([]);
  }, []);

  const onPick = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    take(Array.from(list).map((file) => ({ file, path: toWebkitPath(file) })));
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (running) return;
    // items 只在事件同步阶段有效，先取出来再 await
    const entries: FileSystemEntry[] = [];
    const plain: Array<{ file: File; path: string }> = [];
    for (const item of Array.from(e.dataTransfer.items ?? [])) {
      if (item.kind !== "file") continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry) {
        entries.push(entry);
      } else {
        const file = item.getAsFile();
        if (file) plain.push({ file, path: file.name });
      }
    }
    if (entries.length === 0) {
      take(Array.from(e.dataTransfer.files ?? []).map((file) => ({ file, path: file.name })));
      return;
    }
    try {
      const out: Picked[] = [];
      for (const entry of entries) await walkEntry(entry, "", out);
      take([...out, ...plain]);
    } catch (err) {
      setError(`读取拖入内容失败：${(err as Error).message}`);
    }
  };

  const submit = async () => {
    if (picked.length === 0) return;
    setError(null);
    setJobs([]);
    const form = new FormData();
    // 两个字段各自的顺序要保持一一对应（后端按索引配对）
    for (const item of picked) {
      form.append("paths", item.path);
      form.append("files", item.file, item.file.name);
    }
    try {
      const res = await fetch(`/api/kb/${kbId}/documents/batch`, { method: "POST", body: form });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(data?.detail || `上传失败（HTTP ${res.status}）`);
      }
      const data = (await res.json()) as BatchResponse;
      if (data.skipped?.length) setSkipped((prev) => [...prev, ...(data.skipped ?? [])]);
      setPicked([]);
      setJobIds(data.job_ids?.length ? data.job_ids : [data.job_id]);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const importDir = async () => {
    if (!dirPath.trim()) return;
    setError(null);
    setJobs([]);
    try {
      const res = await fetch(`/api/kb/${kbId}/documents/dir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dirPath.trim() }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(data?.detail || `导入失败（HTTP ${res.status}）`);
      }
      const data = (await res.json()) as BatchResponse;
      setSkipped(data.skipped ?? []);
      setJobIds(data.job_ids?.length ? data.job_ids : [data.job_id]);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  // 多任务并行：每轮把所有分片的进度一起拉回来，全部到终态才停表
  useEffect(() => {
    if (jobIds.length === 0) return;
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const views = await Promise.all(
          jobIds.map(async (id) => {
            const res = await fetch(`/api/jobs/${id}`);
            return res.ok ? ((await res.json()) as JobView) : null;
          }),
        );
        if (!alive) return;
        const got = views.filter((v): v is JobView => v !== null);
        if (got.length) setJobs(got);
        const settled =
          got.length === jobIds.length &&
          got.every((v) => v.status === "done" || v.status === "failed");
        if (settled) {
          setJobIds([]);
          onFinished();
          return;
        }
      } catch {
        /* 网络抖动忽略，下一轮再试 */
      }
      if (alive) timer = window.setTimeout(tick, 1200);
    };
    timer = window.setTimeout(tick, 400);
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [jobIds, onFinished]);

  const sum = (pick: (j: JobView) => number | undefined) =>
    jobs.reduce((acc, j) => acc + (pick(j) ?? 0), 0);
  const total = sum((j) => j.progress?.total ?? j.result?.total);
  const done = sum((j) => j.progress?.done);
  const okCount = sum((j) => j.result?.ok ?? j.progress?.ok);
  const failedCount = sum((j) => j.result?.failed ?? j.progress?.failed);
  const dedupedCount = sum((j) => j.result?.deduped);
  const runningShards = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const finishedAll =
    jobs.length > 0 && jobs.every((j) => j.status === "done" || j.status === "failed");
  const failedJob = jobs.find((j) => j.status === "failed");
  const percent = total
    ? Math.min(100, Math.floor((done * 100) / total))
    : finishedAll ? 100 : 0;
  const failedItems = jobs.flatMap(
    (j) => j.result?.failed_items ?? j.progress?.failed_items ?? [],
  );
  const currentName = jobs.find((j) => j.progress?.current)?.progress?.current;

  return (
    <div className="space-y-3">
      {/* 拖拽区 */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!running) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => void onDrop(e)}
        className={cn(
          "rounded-xl border border-dashed px-4 py-6 text-center transition-colors duration-150",
          dragging
            ? "border-brand bg-brand-soft"
            : "border-line bg-surface-1 hover:border-line-strong",
          running && "pointer-events-none opacity-60",
        )}
      >
        <p className="text-[13px] text-secondary">
          拖入文件或整个文件夹，或
        </p>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          <Button variant="outline" disabled={running} onClick={() => fileInput.current?.click()}>
            选择文件
          </Button>
          <Button variant="outline" disabled={running} onClick={() => dirInput.current?.click()}>
            选择文件夹
          </Button>
          <Button variant="outline" disabled={running} onClick={() => zipInput.current?.click()}>
            选 ZIP 包
          </Button>
        </div>
        <p className="mt-2.5 text-[11.5px] text-tertiary">
          支持 PDF / Word / Excel / PPT / CSV / HTML / Markdown / TXT · 不限文件数 · 单文件 ≤ 50MB ·
          目录结构会保留为文档名
        </p>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={SUPPORTED.join(",")}
          className="hidden"
          onChange={(e) => {
            onPick(e.target.files);
            e.target.value = "";
          }}
        />
        {/* ZIP 走单独入口：单文件 zip 由服务端解包，不能被前端的格式白名单拦掉 */}
        <input
          ref={zipInput}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) {
              setPicked([{ file, path: file.name }]);
              setSkipped([]);
              setJobs([]);
              setError(null);
            }
            e.target.value = "";
          }}
        />
        <input
          ref={dirInput}
          type="file"
          className="hidden"
          onChange={(e) => {
            onPick(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* 已选清单 */}
      {picked.length > 0 && (
        <div className="rounded-lg border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[12.5px] text-secondary">
              已选 <b className="text-primary">{picked.length}</b> 个文件 · {fmtSize(totalBytes)}
              {skipped.length > 0 && ` · ${skipped.length} 个将跳过`}
            </span>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => { setPicked([]); setSkipped([]); }}>
                清空
              </Button>
              <Button disabled={running} onClick={() => void submit()}>
                开始导入
              </Button>
            </div>
          </div>
          <ul className="mt-2 max-h-32 space-y-0.5 overflow-y-auto text-[11.5px] text-tertiary">
            {picked.slice(0, 50).map((p) => (
              <li key={p.path} className="truncate">{p.path}</li>
            ))}
            {picked.length > 50 && <li>…还有 {picked.length - 50} 个</li>}
          </ul>
        </div>
      )}

      {/* 进度 */}
      {(running || jobs.length > 0) && (
        <div className="rounded-lg border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-center justify-between gap-3 text-[12.5px]">
            <span className="min-w-0 truncate text-secondary">
              {finishedAll
                ? `完成：成功 ${okCount}${failedCount ? ` · 失败 ${failedCount}` : ""}${dedupedCount ? ` · 去重命中 ${dedupedCount}` : ""}`
                : `导入 ${done}/${total || "?"}${jobs.length > 1 ? ` · ${Math.min(runningShards || 1, jobs.length)}/${jobs.length} 组并行` : ""}${currentName ? ` · ${currentName}` : ""}`}
            </span>
            <span className="shrink-0 tabular-nums text-tertiary">{percent}%</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-300",
                finishedAll && okCount === 0 && failedCount > 0 ? "bg-danger" : "bg-brand",
              )}
              style={{ width: `${Math.max(2, percent)}%` }}
            />
          </div>
          {failedJob?.error && (
            <p className="mt-2 break-words text-[11.5px] text-danger">{failedJob.error}</p>
          )}
          {failedItems.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-[11.5px] text-danger/90">
              {failedItems.slice(0, 6).map((f) => (
                <li key={f.name} className="truncate">
                  {f.name}：{f.error}
                </li>
              ))}
              {failedItems.length > 6 && <li>…还有 {failedItems.length - 6} 条失败</li>}
            </ul>
          )}
        </div>
      )}

      {/* 跳过明细 */}
      {skipped.length > 0 && (
        <div className="text-[11.5px] text-tertiary">
          <button
            className="transition-colors hover:text-secondary"
            onClick={() => setShowSkipped((v) => !v)}
          >
            {showSkipped ? "收起" : "查看"}跳过的 {skipped.length} 个文件
          </button>
          {showSkipped && (
            <ul className="mt-1.5 max-h-32 space-y-0.5 overflow-y-auto">
              {skipped.slice(0, 50).map((s, i) => (
                <li key={`${s.name}-${i}`} className="truncate">
                  {s.name} · {s.reason}
                </li>
              ))}
              {skipped.length > 50 && <li>…还有 {skipped.length - 50} 个</li>}
            </ul>
          )}
        </div>
      )}

      {/* 服务器目录（本地部署场景，后端默认关闭） */}
      <div className="text-[11.5px]">
        <button
          className="text-tertiary transition-colors hover:text-secondary"
          onClick={() => setDirOpen((v) => !v)}
        >
          {dirOpen ? "收起" : "从服务器目录导入（本地部署）"}
        </button>
        {dirOpen && (
          <div className="mt-2 space-y-2">
            <div className="flex gap-2">
              <input
                value={dirPath}
                onChange={(e) => setDirPath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void importDir()}
                placeholder="绝对路径，例如 D:\docs\产品手册"
                className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface-1 px-3 text-[12.5px] outline-none placeholder:text-tertiary focus:border-line-strong"
              />
              <Button disabled={running || !dirPath.trim()} onClick={() => void importDir()}>
                扫描导入
              </Button>
            </div>
            <p className="text-[11px] text-tertiary">
              文件就地读取、不复制也不修改源目录。需在服务端 .env 配置
              <code className="mx-1 rounded bg-surface-3 px-1">APP_LOCAL_IMPORT_ROOTS</code>
              后才可用。
            </p>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[12.5px] text-danger">
          {error}
        </div>
      )}
    </div>
  );
}
