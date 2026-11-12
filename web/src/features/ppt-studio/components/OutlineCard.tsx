/**
 * 大纲确认卡：analyze 结束后把 LLM 产出的结构化大纲交给用户确认/微调。
 *
 * 纯编辑能力（架构 T04）：加页/删页/加章节、要点行内编辑、页数联动展示。
 * 口径与后端 outline.validate 一致：page_count = 各章节页数之和（内容页），
 * 附录页不计入；总页数 = 内容页 +（policy 为 appendix/both 且有来源时的 1 页附录）。
 *
 * 上半屏常驻设计台（实测预览 + 旋钮）：先让用户看到成稿长什么样，再决定要不要改文字。
 * 内置风格与上传的公司模板共用这套设计台——模板的风格已被抽成一支设计家族。
 */

import { useState } from "react";
import { ChevronDown, ChevronUp, FilePlus2, FolderPlus, Sparkles, Wand2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { LAYOUT_LABEL } from "../design";
import type { DesignCatalog, Outline, OutlinePage, OutlineSection, PageLayout } from "../types";
import { DesignConsole } from "./DesignConsole";

/** 内容页数：与后端 Outline.content_page_count 同口径。 */
export function countContentPages(outline: Outline): number {
  return outline.sections.reduce((sum, section) => sum + section.pages.length, 0);
}

/** 估计总页数：来源策略会追加 1 页附录（与后端 total_slides 口径一致）。 */
export function estimateTotalSlides(outline: Outline): number {
  const appendix =
    (outline.sources_policy === "appendix" || outline.sources_policy === "both") &&
    outline.sources.length > 0
      ? 1
      : 0;
  return countContentPages(outline) + appendix;
}

let pageSeq = 0;
function newPage(title = "新页面"): OutlinePage {
  pageSeq += 1;
  return {
    id: `p-${Date.now()}-${pageSeq}`,
    title,
    kicker: "",
    layout: "auto",
    bullets: [],
    note: "",
    sources: [],
    layout_resolved: "",
  };
}

let sectionSeq = 0;
function newSection(): OutlineSection {
  sectionSeq += 1;
  return { id: `s-${Date.now()}-${sectionSeq}`, title: "新章节", pages: [newPage("章节封面")] };
}

interface Props {
  outline: Outline;
  onChange: (outline: Outline) => void;
  /** 确认并生成（PptStudioPage 里接 generate 流）。 */
  onConfirm: () => void;
  /** 保存修改但不生成（写回消息里的 outline 副本）。 */
  onSave?: () => void;
  busy?: boolean;
  saveBusy?: boolean;
  /** 有会话 id 且是内置风格时才出设计台（预览要按会话取大纲）。 */
  sessionId?: string | null;
  designs?: DesignCatalog | null;
}

export function OutlineCard({
  outline,
  onChange,
  onConfirm,
  onSave,
  busy,
  saveBusy,
  sessionId,
  designs,
}: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const updateSection = (sectionId: string, next: Partial<OutlineSection>) => {
    onChange({
      ...outline,
      sections: outline.sections.map((s) => (s.id === sectionId ? { ...s, ...next } : s)),
    });
  };

  const updatePage = (sectionId: string, pageId: string, next: Partial<OutlinePage>) => {
    const section = outline.sections.find((s) => s.id === sectionId);
    if (!section) return;
    updateSection(sectionId, {
      pages: section.pages.map((p) => (p.id === pageId ? { ...p, ...next } : p)),
    });
  };

  const addPage = (sectionId: string) => {
    const section = outline.sections.find((s) => s.id === sectionId);
    if (!section) return;
    updateSection(sectionId, { pages: [...section.pages, newPage()] });
  };

  const removePage = (sectionId: string, pageId: string) => {
    const section = outline.sections.find((s) => s.id === sectionId);
    if (!section) return;
    updateSection(sectionId, { pages: section.pages.filter((p) => p.id !== pageId) });
  };

  const contentPages = countContentPages(outline);
  const totalSlides = estimateTotalSlides(outline);
  // 与后端校验同源：空标题禁止生成；每页 >8 条要点仅提示（不阻断）
  const titleMissing = !outline.title.trim();
  const overflowPages = outline.sections.flatMap((s) =>
    s.pages.filter((p) => p.bullets.length > 8).map((p) => `${s.title} · ${p.title}`),
  );

  return (
    <div className="w-full max-w-[720px] rounded-xl border border-line bg-surface-1 p-3 sm:p-4">
      {/* 头部：标题 + 页数口径 */}
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <input
            value={outline.title}
            aria-label="演示标题"
            onChange={(e) => onChange({ ...outline, title: e.target.value })}
            placeholder="演示标题（必填）"
            className={cn(
              "w-full rounded-lg border bg-surface-2 px-2.5 py-1.5 text-[14px] font-semibold text-primary outline-none transition-colors duration-150 placeholder:text-placeholder",
              titleMissing ? "border-danger/50" : "border-line",
            )}
          />
          <p className="mt-1.5 text-[12px] text-tertiary">
            共 {outline.sections.length} 章 · 内容页 {contentPages} 页
            {totalSlides !== contentPages && ` · 含附录共 ${totalSlides} 页`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((prev) => ({ ...prev, __all: !prev.__all }))}
          title={collapsed.__all ? "展开全部章节" : "折叠全部章节"}
          className="mt-0.5 shrink-0 rounded-md p-1.5 text-tertiary transition-colors hover:bg-hover hover:text-primary"
        >
          {collapsed.__all ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
      </div>

      {/* 设计台：常驻展开，改一下就能在上面的缩略图里看到 */}
      <div className="mt-3">
        <DesignConsole
          sessionId={sessionId ?? null}
          outline={outline}
          onChange={onChange}
          catalog={designs ?? null}
          disabled={busy}
        />
      </div>

      {/* 章节 + 页面 */}
      <div className="mt-3 space-y-3">
        {outline.sections.map((section, si) => {
          const isCollapsed = collapsed.__all ?? collapsed[section.id] ?? false;
          return (
            <div key={section.id} className="rounded-lg border border-line bg-surface-2">
              <div className="flex items-center gap-2 px-2.5 py-2">
                <span className="shrink-0 text-[12px] font-medium text-tertiary">
                  {si + 1}.
                </span>
                <input
                  value={section.title}
                  aria-label={`章节 ${si + 1} 标题`}
                  onChange={(e) => updateSection(section.id, { title: e.target.value })}
                  className="min-w-0 flex-1 bg-transparent text-[13px] font-medium text-primary outline-none"
                />
                <button
                  type="button"
                  onClick={() => addPage(section.id)}
                  title="在这一章末尾加一页"
                  className="shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-hover hover:text-brand-ink"
                >
                  <FilePlus2 size={13} />
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((prev) => ({ ...prev, [section.id]: !isCollapsed }))
                  }
                  title={isCollapsed ? "展开" : "折叠"}
                  className="shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-hover hover:text-primary"
                >
                  {isCollapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
                </button>
              </div>

              {!isCollapsed && (
                <div className="space-y-2 px-2.5 pb-2.5">
                  {section.pages.map((page, pi) => (
                    <div
                      key={page.id}
                      className="rounded-lg border border-line bg-surface-1 px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="shrink-0 text-[11.5px] text-tertiary">
                          {si + 1}.{pi + 1}
                        </span>
                        <input
                          value={page.title}
                          aria-label={`页面 ${si + 1}.${pi + 1} 标题`}
                          onChange={(e) => updatePage(section.id, page.id, { title: e.target.value })}
                          className="min-w-0 flex-1 bg-transparent text-[12.5px] text-primary outline-none"
                        />
                        <select
                          value={page.layout}
                          aria-label={`页面 ${si + 1}.${pi + 1} 版式`}
                          onChange={(e) =>
                            updatePage(section.id, page.id, { layout: e.target.value as PageLayout })
                          }
                          className="shrink-0 rounded border border-line bg-surface-2 px-1 py-0.5 text-[11px] text-secondary outline-none"
                        >
                          {(Object.keys(LAYOUT_LABEL) as PageLayout[]).map((k) => (
                            <option key={k} value={k}>
                              {LAYOUT_LABEL[k]}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => removePage(section.id, page.id)}
                          title="删除这一页"
                          className="shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger"
                        >
                          <X size={13} />
                        </button>
                      </div>
                      <textarea
                        value={page.bullets.join("\n")}
                        aria-label={`页面 ${si + 1}.${pi + 1} 要点（每行一条）`}
                        rows={Math.max(2, Math.min(page.bullets.length, 5))}
                        placeholder="要点（每行一条，可留空）"
                        onChange={(e) =>
                          updatePage(section.id, page.id, {
                            bullets: e.target.value.split("\n").filter((l) => l.trim() !== ""),
                          })
                        }
                        className="mt-1.5 w-full resize-y rounded-md border border-line bg-surface-2 px-2 py-1.5 text-[12px] leading-5 text-secondary outline-none placeholder:text-placeholder focus:border-brand/40"
                      />
                      {page.bullets.length > 8 && (
                        <p className="mt-1 text-[11px] text-warning">
                          该页 {page.bullets.length} 条要点超出建议上限（8 条），生成时会自动精简
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        <button
          type="button"
          onClick={() => onChange({ ...outline, sections: [...outline.sections, newSection()] })}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-line py-2 text-[12px] text-tertiary transition-colors hover:border-brand/40 hover:text-brand-ink"
        >
          <FolderPlus size={13} /> 添加章节
        </button>
      </div>

      {/* 提示与操作 */}
      {overflowPages.length > 0 && (
        <p className="mt-3 rounded-lg bg-warning/10 px-2.5 py-1.5 text-[11.5px] text-warning">
          {overflowPages.length} 页要点超 8 条：{overflowPages.slice(0, 3).join("、")}
          {overflowPages.length > 3 && " 等"}——可保留，渲染时会自动精简
        </p>
      )}

      <div className="mt-3 flex items-center gap-2">
        {onSave && (
          <button
            type="button"
            onClick={onSave}
            disabled={busy || saveBusy}
            className="rounded-lg border border-line px-3 py-1.5 text-[12.5px] text-secondary transition-colors hover:border-brand/40 hover:text-primary disabled:opacity-50"
          >
            {saveBusy ? "保存中…" : "仅保存"}
          </button>
        )}
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy || saveBusy || titleMissing}
          title={titleMissing ? "请先填写演示标题" : "按当前大纲生成 pptx"}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-brand px-3.5 py-1.5 text-[12.5px] font-medium text-brand-fg transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Wand2 size={13} /> 确认并生成
        </button>
      </div>
      {titleMissing && (
        <p className="mt-2 flex items-center gap-1 text-[11.5px] text-danger">
          <Sparkles size={11} /> 填写标题后才能生成
        </p>
      )}
    </div>
  );
}
