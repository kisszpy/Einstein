/**
 * 资产页「模板」标签：公司模板主题包的集中管理（workspace 级共享，拍板结论 #1）。
 * 上传 .pptx（≤32MB）、色板预览、保真度徽章、详情展开（版式清单 + 字体）、删除。
 * 删除是硬删除但源模板文件长期保留在服务端 data/ppt_templates/<ws>/（拍板结论 #5）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, FileUp, Loader2, Presentation, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";
import { fidelityTone } from "@/features/ppt-studio/components/ResultCard";
import * as api from "@/features/ppt-studio/api";
import type { ThemePackDetail, ThemePackSummary } from "@/features/ppt-studio/types";

type UploadState =
  | { kind: "idle" }
  | { kind: "parsing" }
  | { kind: "error"; message: string };

export function ThemePackPanel() {
  const [packs, setPacks] = useState<ThemePackSummary[]>([]);
  const [detail, setDetail] = useState<ThemePackDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [upload, setUpload] = useState<UploadState>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      setPacks(await api.listThemePacks());
    } catch {
      setPacks([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = async (id: string) => {
    if (detail?.id === id) {
      setDetail(null);
      return;
    }
    try {
      setDetail(await api.getThemePack(id));
    } catch {
      setDetail(null);
    }
  };

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pptx")) {
      setUpload({ kind: "error", message: "只支持 .pptx 模板（≤32MB）" });
      return;
    }
    setUpload({ kind: "parsing" });
    try {
      const pack = await api.uploadThemePack(file);
      setPacks((prev) => [pack, ...prev.filter((p) => p.id !== pack.id)]);
      setUpload({ kind: "idle" });
    } catch (err) {
      setUpload({ kind: "error", message: (err as Error).message });
    }
  };

  const removePack = async (pack: ThemePackSummary) => {
    const ok = await confirmDialog({
      title: "删除主题包",
      message: `删除「${pack.name}」？已生成的 pptx 不受影响，但引用它的会话将回落到默认风格。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    await api.deleteThemePack(pack.id);
    setPacks((prev) => prev.filter((p) => p.id !== pack.id));
    if (detail?.id === pack.id) setDetail(null);
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-[860px]">
        <div className="flex items-center gap-3">
          <div>
            <h2 className="text-[15px] font-medium">公司模板</h2>
            <p className="mt-1 text-[12px] text-tertiary">
              上传 .pptx 模板解析为主题包（配色、字体、版式），工作区内共享，
              AI PPT 制作用它保真渲染。
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".pptx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={upload.kind === "parsing"}
            className="ml-auto flex shrink-0 items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-[12.5px] font-medium text-brand-fg transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {upload.kind === "parsing" ? (
              <>
                <Loader2 size={13} className="animate-spin" /> 解析中…
              </>
            ) : (
              <>
                <FileUp size={13} /> 上传模板
              </>
            )}
          </button>
        </div>

        {upload.kind === "error" && (
          <p className="mt-2 rounded-lg bg-danger/10 px-3 py-2 text-[12px] text-danger">
            {upload.message}
          </p>
        )}

        {loading ? (
          <p className="mt-10 text-center text-[12.5px] text-tertiary">
            <Loader2 size={14} className="mr-1 inline animate-spin" /> 加载中…
          </p>
        ) : packs.length === 0 ? (
          <div className="mt-10 flex flex-col items-center gap-2 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand-ink ">
              <Presentation size={20} strokeWidth={1.6} />
            </span>
            <p className="text-[13.5px] font-medium">还没有公司模板</p>
            <p className="max-w-[420px] text-[12px] leading-relaxed text-tertiary">
              上传一份带母版与版式的 .pptx（建议 ≤32MB），解析后即可在「AI PPT」页选用；
              智能艺术字与复杂图表会在渲染时降级并在保真度报告里说明。
            </p>
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {packs.map((pack) => (
              <div
                key={pack.id}
                className="rounded-xl border border-line bg-surface-1 p-3.5 transition-colors hover:border-brand/40"
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium text-primary">{pack.name}</p>
                    <p className="mt-0.5 text-[11.5px] text-tertiary">
                      {pack.layout_count} 版式 · {pack.status === "ready" ? "就绪" : pack.status === "partial" ? "部分降级" : "解析失败"}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 rounded px-1.5 py-0.5 text-[11px]",
                      fidelityTone(pack.fidelity_score),
                    )}
                  >
                    保真度 {pack.fidelity_score}
                  </span>
                </div>
                <div className="mt-2.5 flex items-center gap-1">
                  {pack.swatches.map((color, i) => (
                    <span
                      key={i}
                      title={color}
                      className="h-4 w-4 rounded border border-line"
                      style={{ backgroundColor: color }}
                    />
                  ))}
                </div>
                <div className="mt-2.5 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void openDetail(pack.id)}
                    className="flex items-center gap-1 text-[12px] text-tertiary transition-colors hover:text-brand-ink"
                  >
                    {detail?.id === pack.id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                    {detail?.id === pack.id ? "收起详情" : "查看详情"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removePack(pack)}
                    title="删除主题包"
                    className="ml-auto rounded p-1 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>

                {detail?.id === pack.id && (
                  <div className="mt-2.5 rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-[11.5px] text-secondary">
                    <p>
                      源文件：{detail.source_filename} · 尺寸 {detail.slide_size.width_in}×
                      {detail.slide_size.height_in} 英寸
                    </p>
                    <p className="mt-1">
                      中文字体：{detail.fonts["ea"] || detail.fonts["latin"] || "跟随模板"}
                    </p>
                    <p className="mt-1">{detail.fidelity.summary_zh}</p>
                    {detail.fidelity.degradations.length > 0 && (
                      <ul className="mt-1 space-y-0.5">
                        {detail.fidelity.degradations.map((d, i) => (
                          <li key={i} className="text-warning">· {d}</li>
                        ))}
                      </ul>
                    )}
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {detail.layouts.map((layout) => (
                        <span
                          key={layout.index}
                          title={layout.has_title && layout.has_body ? "含标题与正文占位符" : "版式占位符不全，渲染时自动匹配近似版式"}
                          className="rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-tertiary"
                        >
                          {layout.name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
