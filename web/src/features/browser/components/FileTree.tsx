/**
 * 项目文件树：由文件路径**自建目录结构**，目录可折叠，按扩展名给图标。
 *
 * 为什么不依赖后端返回 dir 条目：`/api/projects/{id}/artifacts` 只回文件
 * （产物清单天然是文件集合），`/tree` 虽有目录但浏览页不用。就地构建目录
 * 节点才能让两处入口共用同一个组件，也不会因为缺 dir 条目退化成缩进列表
 * ——之前就是这样：40 多个 .java 平铺成一长条，看不出模块结构。
 *
 * 目录链压缩：Java 包路径动辄五层（com/shop/common/api/Result.java），
 * 逐层展开要点四次才能看到文件。只剩一个子目录且自己没文件时，把这段
 * 链并成一行 `com.shop.common.api`（VSCode 的 compact folders 同款）。
 */

import { useCallback, useMemo, useState } from "react";
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
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { TreeEntry } from "../types";

function iconFor(path: string) {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (["html", "htm"].includes(ext)) return FileCode2;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return FileImage;
  if (ext === "pptx") return Presentation;
  if (["csv", "xlsx", "tsv"].includes(ext)) return Table2;
  if (["json", "yaml", "yml", "toml", "properties", "xml"].includes(ext)) return FileJson;
  if (["md", "txt", "pdf", "docx"].includes(ext)) return FileText;
  return File;
}

interface DirNode {
  kind: "dir";
  name: string;
  path: string;
  children: TreeNode[];
}

interface FileNode {
  kind: "file";
  name: string;
  path: string;
  entry: TreeEntry;
}

type TreeNode = DirNode | FileNode;

/** 把平铺的路径列表折叠成树；目录节点从路径里现造。 */
function buildTree(entries: TreeEntry[]): DirNode {
  const root: DirNode = { kind: "dir", name: "", path: "", children: [] };
  const dirs = new Map<string, DirNode>([["", root]]);

  const ensureDir = (segments: string[], count: number): DirNode => {
    const path = segments.slice(0, count).join("/");
    const hit = dirs.get(path);
    if (hit) return hit;
    const parent = count > 0 ? ensureDir(segments, count - 1) : root;
    const node: DirNode = {
      kind: "dir",
      name: segments[count - 1] ?? "",
      path,
      children: [],
    };
    parent.children.push(node);
    dirs.set(path, node);
    return node;
  };

  for (const entry of entries) {
    const segments = entry.path.split("/").filter(Boolean);
    if (segments.length === 0) continue;
    // 后端若给了目录条目，只登记，文件条目再把内容挂上去
    ensureDir(segments, segments.length - (entry.type === "dir" ? 0 : 1));
    if (entry.type === "dir") continue;
    const parent = dirs.get(segments.slice(0, -1).join("/")) ?? root;
    const node: FileNode = {
      kind: "file",
      name: segments[segments.length - 1] ?? entry.path,
      path: entry.path,
      entry,
    };
    if (!parent.children.some((c) => c.kind === "file" && c.path === node.path)) {
      parent.children.push(node);
    }
  }

  const sort = (node: DirNode) => {
    // 目录在前、文件在后，同类按名称——和文件管理器的心智一致
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, "zh");
    });
    for (const child of node.children) if (child.kind === "dir") sort(child);
  };
  sort(root);
  return root;
}

/** 单链目录压缩：只有唯一子目录、自己没有文件时并成一行。 */
function compactDir(node: DirNode): { node: DirNode; label: string } {
  let current = node;
  const names = [node.name];
  for (;;) {
    const only = current.children[0];
    if (current.children.length !== 1 || !only || only.kind !== "dir") break;
    current = only;
    names.push(current.name);
  }
  return { node: current, label: names.join(".") };
}

interface RowProps {
  node: TreeNode;
  depth: number;
  active: string;
  collapsed: Set<string>;
  onToggle: (path: string) => void;
  onPick: (path: string) => void;
  onDelete?: (path: string) => void;
}

function Row({ node, depth, active, collapsed, onToggle, onPick, onDelete }: RowProps) {
  if (node.kind === "file") {
    const Icon = iconFor(node.path);
    const selected = active === node.path;
    return (
      <div
        className={cn(
          "group flex w-full items-center transition-colors",
          selected ? "bg-brand-soft" : "hover:bg-surface-3",
        )}
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <button
          onClick={() => onPick(node.path)}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-1.5 py-[5px] pr-1 text-left text-[12.5px]",
            selected ? "text-brand-ink" : "text-secondary group-hover:text-primary",
          )}
          title={node.path}
        >
          <span className="w-[11px] shrink-0" />
          <Icon size={13} strokeWidth={1.75} className="shrink-0 opacity-70" />
          <span className="truncate">{node.name}</span>
          {node.entry.size && (
            <span className="ml-auto shrink-0 pl-2 text-[10px] text-tertiary opacity-0 group-hover:opacity-100">
              {node.entry.size}
            </span>
          )}
        </button>
        {onDelete && (
          <button
            type="button"
            onClick={() => onDelete(node.path)}
            title="删除这个文件"
            aria-label={`删除 ${node.name}`}
            className="mr-1 hidden shrink-0 rounded p-1 text-tertiary transition-colors hover:bg-danger/10 hover:text-danger group-hover:block focus:block"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>
    );
  }

  const { node: shown, label } = compactDir(node);
  const open = !collapsed.has(shown.path);
  return (
    <>
      <div
        className="group flex w-full items-center hover:bg-surface-3"
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <button
          onClick={() => onToggle(shown.path)}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-[5px] pr-1 text-left text-[12.5px] text-secondary group-hover:text-primary"
          title={shown.path}
        >
          <ChevronRight
            size={11}
            className={cn("shrink-0 text-tertiary transition-transform", open && "rotate-90")}
          />
          {open ? (
            <FolderOpen size={13} strokeWidth={1.75} className="shrink-0 opacity-70" />
          ) : (
            <Folder size={13} strokeWidth={1.75} className="shrink-0 opacity-70" />
          )}
          <span className="truncate">{label}</span>
        </button>
      </div>
      {open &&
        shown.children.map((child) => (
          <Row
            key={`${child.kind}:${child.path}`}
            node={child}
            depth={depth + 1}
            active={active}
            collapsed={collapsed}
            onToggle={onToggle}
            onPick={onPick}
            onDelete={onDelete}
          />
        ))}
    </>
  );
}

export function FileTree({
  entries,
  active,
  onPick,
  onDelete,
}: {
  entries: TreeEntry[];
  active: string;
  onPick: (path: string) => void;
  onDelete?: (path: string) => void;
}) {
  // 存「已折叠」而不是「已展开」：默认全展开，用户看到的是完整结构
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const tree = useMemo(() => buildTree(entries), [entries]);

  if (entries.length === 0) {
    return <p className="px-3 py-4 text-[12px] text-tertiary">这个项目还没有文件</p>;
  }

  return (
    <div className="py-1">
      {tree.children.map((child) => (
        <Row
          key={`${child.kind}:${child.path}`}
          node={child}
          depth={0}
          active={active}
          collapsed={collapsed}
          onToggle={toggle}
          onPick={onPick}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
