import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { VisibilitySelect, VISIBILITY_HINT, type Visibility } from "@/components/ui/visibility";

/** 新建知识库弹窗：名称 / 描述 / 可见范围。 */
export function CreateKbDialog({
  open,
  name,
  desc,
  visibility,
  busy,
  error,
  onNameChange,
  onDescChange,
  onVisibilityChange,
  onSubmit,
  onClose,
}: {
  open: boolean;
  name: string;
  desc: string;
  visibility: Visibility;
  busy: boolean;
  error: string | null;
  onNameChange: (value: string) => void;
  onDescChange: (value: string) => void;
  onVisibilityChange: (value: Visibility) => void;
  onSubmit: () => void;
  onClose: () => void;
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      onClick={onClose}
    >
      <Card className="w-full max-w-[420px] space-y-3 p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[13px] font-medium">新建知识库</h3>
        <input
          autoFocus
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void onSubmit()}
          placeholder="知识库名称"
          className="h-9 w-full rounded-lg border border-line bg-surface-1 px-3 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
        />
        <textarea
          value={desc}
          onChange={(e) => onDescChange(e.target.value)}
          placeholder="描述（可选）"
          rows={2}
          className="w-full resize-y rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] outline-none placeholder:text-tertiary focus:border-line-strong"
        />
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-tertiary">可见范围</span>
            <VisibilitySelect value={visibility} onChange={onVisibilityChange} />
          </div>
          <span className="text-[12px] text-tertiary">{VISIBILITY_HINT[visibility]}</span>
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button disabled={busy || !name.trim()} onClick={() => void onSubmit()}>
            创建
          </Button>
        </div>
      </Card>
    </div>
  );
}
