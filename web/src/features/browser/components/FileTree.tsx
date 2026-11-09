/** 项目文件树：目录可折叠，按扩展名给图标。 */

import { useMemo, useState } from "react";
import {
  ChevronRight,
  File,
  FileCode2,
  FileImage,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  Presentation,
  Table2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { TreeEntry } from "../types";

function iconFor(path: string) {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (["html", "htm"].includes(ext)) return FileCode2;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return FileImage;
  if (ext === "pptx") return Presentation;
  if (["csv", "xlsx", "tsv"].includes(ext)) return Table2;
  if (["json", "yaml", "yml", "toml"].includes(ext)) return FileJson;
  if (["md", "txt", "pdf", "docx"].includes(ext)) return FileText;
  return File;
}

export function FileTree({
  entries,
  active,
  onPick,
}: {
  entries: TreeEntry[];
  active: string;
  onPick: (path: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const visible = useMemo(
    () =>
      entries.filter((entry) => {
        const parts = entry.path.split("/");
        for (let i = 1; i < parts.length; i += 1) {
          if (collapsed.has(parts.slice(0, i).join("/"))) return false;
        }
        return true;
      }),
    [entries, collapsed],
  );

  return (
    <div className="py-1">
      {visible.map((entry) => {
        const depth = entry.path.split("/").length - 1;
        const isDir = entry.type === "dir";
        const isOpen = !collapsed.has(entry.path);
        const Icon = isDir ? (isOpen ? FolderOpen : Folder) : iconFor(entry.path);
        return (
          <button
            key={entry.path}
            onClick={() => {
              if (isDir) {
                setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(entry.path)) next.delete(entry.path);
                  else next.add(entry.path);
                  return next;
                });
              } else {
                onPick(entry.path);
              }
            }}
            className={cn(
              "group flex w-full items-center gap-1.5 py-[5px] pr-2 text-left text-[12.5px] transition-colors",
              active === entry.path
                ? "bg-brand-soft text-brand-deep dark:text-brand"
                : "text-secondary hover:bg-surface-3 hover:text-primary",
            )}
            style={{ paddingLeft: 8 + depth * 12 }}
            title={entry.path}
          >
            {isDir ? (
              <ChevronRight
                size={11}
                className={cn(
                  "shrink-0 text-tertiary transition-transform",
                  isOpen && "rotate-90",
                )}
              />
            ) : (
              <span className="w-[11px] shrink-0" />
            )}
            <Icon size={13} strokeWidth={1.75} className="shrink-0 opacity-70" />
            <span className="truncate">{entry.path.split("/").pop()}</span>
            {!isDir && entry.size && (
              <span className="ml-auto shrink-0 text-[10px] text-tertiary opacity-0 group-hover:opacity-100">
                {entry.size}
              </span>
            )}
          </button>
        );
      })}
      {visible.length === 0 && (
        <p className="px-3 py-4 text-[12px] text-tertiary">这个项目还没有文件</p>
      )}
    </div>
  );
}
