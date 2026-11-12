import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { VisibilityBadge } from "@/components/ui/visibility";
import { ReadonlyBadge } from "@/features/kb/kb-atoms";
import type { KBItem } from "@/features/kb/kb-types";
import { cn } from "@/lib/utils";

/** 左侧知识库列表：选中态、可见范围、只读标记、删除入口。 */
export function KbSidebar({
  kbs,
  selected,
  onSelect,
  onDelete,
  onCreate,
}: {
  kbs: KBItem[];
  selected: string | null;
  onSelect: (id: string) => void;
  onDelete: (kb: KBItem) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="hidden w-72 shrink-0 overflow-y-auto border-r border-line bg-surface-2 p-4 md:block">
      <h2 className="mb-3 text-[13px] font-medium">知识库</h2>
      <div className="space-y-1">
        {kbs.map((kb) => (
          <div key={kb.id} className="group flex items-center gap-1">
            <button
              onClick={() => onSelect(kb.id)}
              className={cn(
                "min-w-0 flex-1 rounded-lg px-3 py-2 text-left transition-colors duration-150",
                selected === kb.id
                  ? "bg-brand-soft text-brand-ink "
                  : "text-secondary hover:bg-surface-3 hover:text-primary",
              )}
            >
              <div className="flex items-center gap-1.5">
                <span className="min-w-0 truncate text-[13px]">{kb.name}</span>
                <VisibilityBadge value={kb.visibility} className="shrink-0" />
                {kb.my_role === "read" && <ReadonlyBadge />}
              </div>
              <div className="text-xs opacity-70">
                {kb.document_count} 篇文档{kb.mine ? "" : " · 共享"}
              </div>
            </button>
            {selected === kb.id && kb.my_role !== "read" && (
              <button
                title="删除知识库"
                aria-label="删除知识库"
                onClick={() => void onDelete(kb)}
                className="shrink-0 rounded-md p-1.5 text-tertiary opacity-0 transition-all duration-150 hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Trash2 size={13} strokeWidth={1.75} />
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-4">
        <Button variant="outline" className="w-full" onClick={onCreate}>
          新建知识库
        </Button>
      </div>
    </aside>
  );
}
