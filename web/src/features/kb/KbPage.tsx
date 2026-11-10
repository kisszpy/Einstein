import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { VisibilityBadge, VisibilitySelect } from "@/components/ui/visibility";
import { BatchImport } from "@/features/kb/BatchImport";
import { CreateKbDialog } from "@/features/kb/CreateKbDialog";
import { DocList } from "@/features/kb/DocList";
import { GrantsPanel } from "@/features/kb/GrantsPanel";
import { ReadonlyBadge } from "@/features/kb/kb-atoms";
import { KbSidebar } from "@/features/kb/KbSidebar";
import { SearchBench } from "@/features/kb/SearchBench";
import { useKbWorkspace } from "@/features/kb/use-kb-workspace";
import { ShareDialog } from "@/features/share/ShareDialog";

/**
 * 知识库工作台。
 *
 * 页面本身只负责布局，状态与请求都在 useKbWorkspace 里；三块可独立演进的
 * 区域（左侧库列表 / 文档列表含切片与版本面板 / 检索测试台）各自成组件。
 * 这么拆的判据是「出错时能不能单独定位」：切片保存失败、批量重抓部分失败、
 * 检索模式对比不准，这三类问题的排查路径完全不同，混在一个文件里只会越改越乱。
 */
export function KbPage() {
  const w = useKbWorkspace();
  const currentKb = w.currentKb;

  return (
    <div className="flex h-full">
      <KbSidebar
        kbs={w.kbs}
        selected={w.selected}
        onSelect={w.setSelected}
        onDelete={(kb) => void w.removeKb(kb)}
        onCreate={() => w.setCreateOpen(true)}
      />

      {/* 详情区 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {!w.selected ? (
          <div className="flex h-full items-center justify-center text-sm text-tertiary">
            选择或创建一个知识库开始
          </div>
        ) : (
          <div className="mx-auto max-w-[860px] space-y-6 px-8 py-8">
            {currentKb && (
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-[15px] font-medium">{currentKb.name}</h2>
                    <VisibilityBadge value={currentKb.visibility} />
                    {currentKb.my_role === "read" && <ReadonlyBadge />}
                  </div>
                  {currentKb.description && (
                    <p className="mt-1 text-[12.5px] text-secondary">{currentKb.description}</p>
                  )}
                </div>
                {w.canWrite && (
                  <div className="flex shrink-0 items-center gap-2">
                    <Button variant="outline" onClick={() => w.setGrantsOpen(true)}>
                      共享给成员
                    </Button>
                    <Button variant="outline" onClick={() => w.setShareOpen(true)}>
                      对外分享
                    </Button>
                    {currentKb.mine && (
                      <label className="flex items-center gap-2 text-xs text-tertiary">
                        可见范围
                        <VisibilitySelect
                          value={currentKb.visibility}
                          disabled={w.busy}
                          onChange={(v) => void w.patchKb(currentKb, { visibility: v })}
                        />
                      </label>
                    )}
                  </div>
                )}
              </div>
            )}
            {w.notice && (
              <div className="rounded-lg border border-brand/40 bg-brand-soft px-3.5 py-2.5 text-[12.5px] text-brand-deep dark:text-brand">
                {w.notice}
              </div>
            )}
            {w.error && (
              <div className="flex items-start justify-between gap-3 rounded-lg border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[12.5px] text-danger">
                <span className="min-w-0 break-words">{w.error}</span>
                <button
                  onClick={() => w.setError(null)}
                  className="shrink-0 text-[12px] opacity-70 transition-opacity hover:opacity-100"
                >
                  关闭
                </button>
              </div>
            )}

            {/* 添加文档：整目录导入 */}
            {w.canWrite && (
              <Card className="p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="text-[13px] font-medium">添加文档</h3>
                  <span className="text-[12px] text-tertiary">可整目录导入</span>
                </div>
                <div className="mt-3">
                  <BatchImport kbId={w.selected} onFinished={() => void w.refreshDocs()} />
                </div>
              </Card>
            )}

            {/* 手动粘贴：保留小文本快速验证的场景 */}
            {w.canWrite && (
              <Card className="p-5">
                <details>
                  <summary className="cursor-pointer text-[13px] font-medium">手动粘贴文本</summary>
                  <div className="mt-3 flex items-center gap-3">
                    <input
                      value={w.docName}
                      onChange={(e) => w.setDocName(e.target.value)}
                      placeholder="文档名称"
                      className="h-9 w-48 rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                    />
                  </div>
                  <textarea
                    value={w.docContent}
                    onChange={(e) => w.setDocContent(e.target.value)}
                    placeholder="粘贴文档内容，将自动切片并向量化…"
                    rows={4}
                    className="mt-3 w-full resize-y rounded-lg border border-line bg-surface-1 p-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
                  />
                  <div className="mt-3 flex justify-end">
                    <Button
                      disabled={w.busy || !w.docName.trim() || !w.docContent.trim()}
                      onClick={() => void w.addDoc()}
                    >
                      入库
                    </Button>
                  </div>
                </details>
              </Card>
            )}

            <DocList
              docs={w.docs}
              canWrite={w.canWrite}
              batchBusy={w.batchBusy}
              batchResult={w.batchResult}
              onRefreshBatch={(staleOnly) => void w.refreshBatch(staleOnly)}
              onDismissBatch={() => w.setBatchResult(null)}
              openDoc={w.openDoc}
              panel={w.panel}
              chunks={w.chunks}
              chunkDrafts={w.chunkDrafts}
              versions={w.versions}
              panelBusy={w.panelBusy}
              rowBusy={w.rowBusy}
              onTogglePanel={(doc, next) => void w.togglePanel(doc, next)}
              onClosePanel={() => w.setOpenDoc(null)}
              onChunkDraft={(chunkId, text) =>
                w.setChunkDrafts((prev) => ({ ...prev, [chunkId]: text }))
              }
              onSaveChunk={(chunk) => void w.saveChunk(chunk)}
              onRefreshDoc={(doc) => void w.refreshDoc(doc)}
              onReindexDoc={(doc) => void w.reindexDoc(doc)}
              onRemoveDoc={(doc) => void w.removeDoc(doc)}
              onRollback={(doc, version) => void w.rollbackDoc(doc, version)}
            />

            <SearchBench
              query={w.query}
              mode={w.mode}
              hits={w.hits}
              compare={w.compare}
              busy={w.busy}
              onQueryChange={w.setQuery}
              onModeChange={w.setMode}
              onSearch={() => void w.runSearch()}
              onCompare={() => void w.runCompare()}
              onDismissCompare={() => w.setCompare(null)}
            />
          </div>
        )}
      </div>

      {/* 共享给成员 */}
      {w.grantsOpen && w.selected && (
        <GrantsPanel kbId={w.selected} onClose={() => w.setGrantsOpen(false)} />
      )}

      <CreateKbDialog
        open={w.createOpen}
        name={w.newKbName}
        desc={w.newKbDesc}
        visibility={w.newKbVisibility}
        busy={w.busy}
        error={w.createError}
        onNameChange={w.setNewKbName}
        onDescChange={w.setNewKbDesc}
        onVisibilityChange={w.setNewKbVisibility}
        onSubmit={() => void w.createKb()}
        onClose={() => w.setCreateOpen(false)}
      />

      {currentKb && (
        <ShareDialog
          open={w.shareOpen}
          onClose={() => w.setShareOpen(false)}
          kind="kb"
          targetId={currentKb.id}
          title={currentKb.name}
        />
      )}
    </div>
  );
}
