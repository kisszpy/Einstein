/**
 * 底部输入器：与 WorkBuddy 一致的圆角容器 + 左下「+」菜单 + 右下发送/停止。
 * 能力、知识库、项目都收进「+」菜单，主界面只留输入。
 */

import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  Database,
  FolderPlus,
  Plus,
  Sparkles,
} from "lucide-react";
import {
  ComposerFrame,
  ComposerInput,
  ComposerSendButton,
  ComposerToolbar,
} from "@/components/ui/composer";
import { cn } from "@/lib/utils";
import type { KbInfo, ProjectInfo, ToolInfo } from "../types";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  running: boolean;
  capabilities: ToolInfo[];
  caps: string[];
  onToggleCap: (id: string) => void;
  kbs: KbInfo[];
  selectedKbs: string[];
  onToggleKb: (id: string) => void;
  projects: ProjectInfo[];
  project: string;
  onSelectProject: (id: string) => void;
  onCreateProject: () => void;
  placeholder?: string;
  autoFocus?: boolean;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  running,
  capabilities,
  caps,
  onToggleCap,
  kbs,
  selectedKbs,
  onToggleKb,
  projects,
  project,
  onSelectProject,
  onCreateProject,
  placeholder = "描述你的任务，Enter 发送，Shift + Enter 换行",
  autoFocus,
}: Props) {
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  const canSend = value.trim().length > 0;

  return (
    <div className="w-full">
      <ComposerFrame data-composer-frame>
        <ComposerInput
          value={value}
          onChange={onChange}
          onSubmit={() => {
            if (!running) onSubmit();
          }}
          autoFocus={autoFocus}
          data-agent-composer
          placeholder={placeholder}
          className="min-h-[52px]"
        />

        <ComposerToolbar
          left={
            <>
              <div className="relative">
                {menu && (
                  <div className="fixed inset-0 z-30" onClick={() => setMenu(false)} aria-hidden />
                )}
                <button
                  type="button"
                  onClick={() => setMenu((v) => !v)}
                  title="添加能力 / 知识库 / 项目"
                  aria-label="添加能力 / 知识库 / 项目"
                  className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-full transition-colors duration-150",
                    menu
                      ? "bg-surface-3 text-primary"
                      : "text-secondary hover:bg-hover hover:text-primary",
                  )}
                >
                  <Plus size={15} strokeWidth={2} />
                </button>

                {menu && (
                  <div className="absolute bottom-full left-0 z-40 mb-2 w-[288px] overflow-hidden rounded-xl border border-line bg-surface-1 shadow-float">
                    <div className="max-h-[380px] overflow-y-auto p-1.5">
                      <p className="px-2 pb-1 pt-1.5 text-[11px] font-medium text-tertiary">能力</p>
                      {capabilities.map((cap) => {
                        const on = caps.includes(cap.id);
                        return (
                          <button
                            key={cap.id}
                            onClick={() => onToggleCap(cap.id)}
                            className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150 hover:bg-hover"
                          >
                            <span
                              className={cn(
                                "mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[4px] border transition-colors",
                                on ? "border-brand bg-brand text-white" : "border-line-strong",
                              )}
                            >
                              {on && <Check size={10} strokeWidth={3} />}
                            </span>
                            <span className="min-w-0">
                              <span className="block text-[12.5px] text-primary">{cap.name}</span>
                              <span className="block truncate text-[11px] text-tertiary">
                                {cap.description}
                              </span>
                            </span>
                          </button>
                        );
                      })}

                      {kbs.length > 0 && (
                        <>
                          <p className="mt-1 border-t border-line px-2 pb-1 pt-2 text-[11px] font-medium text-tertiary">
                            知识库
                          </p>
                          {kbs.map((kb) => {
                            const on = selectedKbs.includes(kb.id);
                            return (
                              <button
                                key={kb.id}
                                onClick={() => onToggleKb(kb.id)}
                                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150 hover:bg-hover"
                              >
                                <span
                                  className={cn(
                                    "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[4px] border transition-colors",
                                    on ? "border-brand bg-brand text-white" : "border-line-strong",
                                  )}
                                >
                                  {on && <Check size={10} strokeWidth={3} />}
                                </span>
                                <Database size={12} className="shrink-0 text-tertiary" />
                                <span className="min-w-0 flex-1 truncate text-[12.5px] text-primary">
                                  {kb.name}
                                </span>
                                <span className="shrink-0 text-[11px] text-tertiary">
                                  {kb.document_count}
                                </span>
                              </button>
                            );
                          })}
                        </>
                      )}

                      <p className="mt-1 border-t border-line px-2 pb-1 pt-2 text-[11px] font-medium text-tertiary">
                        项目
                      </p>
                      <button
                        onClick={() => {
                          onSelectProject("");
                          setMenu(false);
                        }}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150 hover:bg-hover"
                      >
                        <span className="flex-1 text-[12.5px] text-primary">由智能体创建</span>
                        {!project && <Check size={12} className="text-brand" />}
                      </button>
                      {projects.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => {
                            onSelectProject(p.id);
                            setMenu(false);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150 hover:bg-hover"
                        >
                          <span className="min-w-0 flex-1 truncate text-[12.5px] text-primary">
                            {p.name}
                          </span>
                          <span className="shrink-0 text-[11px] text-tertiary">{p.files} 文件</span>
                          {project === p.id && <Check size={12} className="text-brand" />}
                        </button>
                      ))}
                      <button
                        onClick={() => {
                          setMenu(false);
                          onCreateProject();
                        }}
                        className="mt-0.5 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12.5px] text-secondary transition-colors duration-150 hover:bg-hover hover:text-primary"
                      >
                        <FolderPlus size={13} className="text-tertiary" />
                        新建项目
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* 已选凭据，和 WorkBuddy 的 chip 一样贴在「+」右侧 */}
              {caps.length > 0 && (
                <span className="inline-flex h-6 items-center gap-1 rounded-full bg-surface-3 px-2 text-[11.5px] text-secondary">
                  {caps.length} 项能力
                </span>
              )}
              {selectedKbs.length > 0 && (
                <span className="inline-flex h-6 items-center gap-1 rounded-full bg-surface-3 px-2 text-[11.5px] text-secondary">
                  知识库 {selectedKbs.length}
                </span>
              )}
              {project && (
                <span className="hidden h-6 items-center gap-1 rounded-full bg-surface-3 px-2 text-[11.5px] text-secondary sm:inline-flex">
                  {project}
                </span>
              )}
            </>
          }
          right={
            <>
              <span
                title="工作型智能体：自主编排工具，过程与产物全程可见"
                className="hidden items-center gap-1 rounded-full px-2 py-1 text-[11.5px] text-tertiary transition-colors hover:bg-hover hover:text-secondary sm:inline-flex"
              >
                <Sparkles size={12} strokeWidth={1.9} />
                工作型智能体
                <ChevronDown size={11} strokeWidth={2} />
              </span>
              <ComposerSendButton
                running={running}
                disabled={!canSend}
                onSend={onSubmit}
                onStop={onStop}
                labels={{ send: "发送", stop: "停止运行" }}
              />
            </>
          }
        />
      </ComposerFrame>
      <p className="mt-2 text-center text-[11px] text-tertiary">
        智能体会调用工具并写入项目文件，重要结论请核对
      </p>
    </div>
  );
}
