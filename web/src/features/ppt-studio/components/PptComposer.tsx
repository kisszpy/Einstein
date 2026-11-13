/**
 * 底部输入区：需求描述 + 专家（默认 PPT 美化专家）+ Skills 多选 + 公司模板 + 落盘项目。
 * 专家/Skills 列表直接 fetch /api/experts、/api/skills（与 ExpertsPage 同源）；
 * 项目列表来自 browser/api.listProjects，新建项目走 POST /api/projects。
 */

import { useEffect, useRef, useState } from "react";
import { Briefcase, ChevronDown, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import { promptDialog } from "@/lib/confirm";
import { listProjects } from "@/features/browser/api";
import type { ProjectInfo } from "@/features/browser/types";
import {
  ComposerFrame,
  ComposerInput,
  ComposerSendButton,
  ComposerToolbar,
} from "@/components/ui/composer";
import { TemplatePicker } from "./TemplatePicker";
import type { ThemePackSummary } from "../types";

/** 预置 PPT 专家的稳定 id（与后端 PRESET_EXPERTS 对齐）。 */
export const DEFAULT_PPT_EXPERT_ID = "ppt-beautifier";

interface ExpertOption {
  id: string;
  name: string;
  emoji?: string;
}

interface SkillOption {
  id: string;
  name: string;
}

interface Props {
  prompt: string;
  onPromptChange: (v: string) => void;
  expertId: string;
  onExpertChange: (id: string) => void;
  skillIds: string[];
  onSkillIdsChange: (ids: string[]) => void;
  themePackId: string;
  onThemePackChange: (id: string) => void;
  packs: ThemePackSummary[];
  onPacksChange: (packs: ThemePackSummary[]) => void;
  project: string;
  onProjectChange: (name: string) => void;
  projects: ProjectInfo[];
  onProjectsChange: (projects: ProjectInfo[]) => void;
  onSubmit: () => void;
  busy?: boolean;
  placeholder?: string;
}

export function PptComposer(props: Props) {
  const {
    prompt,
    onPromptChange,
    expertId,
    onExpertChange,
    skillIds,
    onSkillIdsChange,
    themePackId,
    onThemePackChange,
    packs,
    onPacksChange,
    project,
    onProjectChange,
    projects,
    onProjectsChange,
    onSubmit,
    busy,
    placeholder = "描述要做一份什么演示：主题、受众、页数偏好…",
  } = props;

  const [experts, setExperts] = useState<ExpertOption[]>([]);
  const [skills, setSkills] = useState<SkillOption[]>([]);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const skillsBoxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [e, s] = await Promise.all([
          fetch("/api/experts").then((r) => r.json()),
          fetch("/api/skills").then((r) => r.json()),
        ]);
        if (alive) {
          setExperts(Array.isArray(e) ? e : []);
          setSkills(Array.isArray(s) ? s : []);
        }
      } catch {
        /* 列表拉不到不阻塞输入：专家下拉只剩当前值 */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 点外面收起 Skills 多选浮层
  useEffect(() => {
    if (!skillsOpen) return;
    const onDown = (e: MouseEvent) => {
      if (skillsBoxRef.current && !skillsBoxRef.current.contains(e.target as Node)) {
        setSkillsOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [skillsOpen]);

  const toggleSkill = (id: string) => {
    onSkillIdsChange(
      skillIds.includes(id) ? skillIds.filter((s) => s !== id) : [...skillIds, id],
    );
  };

  const createProject = async () => {
    const name = await promptDialog({
      title: "新建项目",
      message: "项目名称支持字母、数字、中文与连字符",
      placeholder: "project-name",
      confirmLabel: "创建",
    });
    if (!name || !name.trim()) return;
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), template: "blank" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const info = (await res.json()) as { project: string };
      // 后端返回项目名；重拉列表保证选择器数据与资产页同源
      const fresh = await listProjects();
      onProjectsChange(fresh);
      onProjectChange(info.project);
    } catch {
      /* 新建失败保持原选择，用户可手动选已有项目 */
    }
  };

  const canSubmit = !busy && prompt.trim().length > 0;

  return (
    <div className="w-full pb-3">
      <div className="conversation-column">
        <ComposerFrame data-composer-frame>
        <ComposerInput
          value={prompt}
          onChange={onPromptChange}
          onSubmit={() => {
            if (canSubmit) onSubmit();
          }}
          placeholder={placeholder}
          className="min-h-[52px]"
        />

        <ComposerToolbar
          left={
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              {/* 专家选择 */}
              <select
                value={expertId}
                aria-label="选择专家"
                disabled={busy}
                onChange={(e) => onExpertChange(e.target.value)}
                className={cn(
                  "h-7 max-w-[170px] truncate rounded-full border border-line bg-transparent px-2.5 text-[12px] text-secondary outline-none transition-colors hover:border-line-strong disabled:opacity-50",
                  !expertId && "text-placeholder",
                )}
              >
                <option value="">通用助手…</option>
                {experts.map((expert) => (
                  <option key={expert.id} value={expert.id}>
                    {expert.emoji ? `${expert.emoji} ` : ""}
                    {expert.name}
                  </option>
                ))}
              </select>

              {/* Skills 多选浮层 */}
              <div ref={skillsBoxRef} className="relative">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setSkillsOpen((v) => !v)}
                  title="附加技能（可多选）"
                  className={cn(
                    "flex h-7 items-center gap-1 rounded-full border border-line bg-transparent px-2.5 text-[12px] text-secondary transition-colors hover:border-line-strong disabled:opacity-50",
                    skillsOpen && "border-line-strong text-primary",
                  )}
                >
                  <Wrench size={12} strokeWidth={1.9} />
                  技能{skillIds.length > 0 && ` · ${skillIds.length}`}
                  <ChevronDown size={11} />
                </button>
                {skillsOpen && (
                  <div className="absolute bottom-full left-0 z-20 mb-1.5 max-h-[220px] w-[240px] overflow-y-auto rounded-xl border border-line bg-surface-1 p-1.5 shadow-float">
                    {skills.length === 0 && (
                      <p className="px-2 py-2 text-[12px] text-tertiary">暂无可用技能</p>
                    )}
                    {skills.map((skill) => (
                      <label
                        key={skill.id}
                        className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] text-secondary transition-colors hover:bg-hover"
                      >
                        <input
                          type="checkbox"
                          checked={skillIds.includes(skill.id)}
                          onChange={() => toggleSkill(skill.id)}
                          className="accent-brand"
                        />
                        <span className="truncate">{skill.name}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <TemplatePicker
                value={themePackId}
                onChange={onThemePackChange}
                packs={packs}
                onPacksChange={onPacksChange}
                disabled={busy}
              />

              {/* 落盘项目 */}
              <select
                value={project}
                aria-label="选择落盘项目"
                disabled={busy}
                onChange={(e) => onProjectChange(e.target.value)}
                className={cn(
                  "h-7 max-w-[170px] truncate rounded-full border border-line bg-transparent px-2.5 text-[12px] text-secondary outline-none transition-colors hover:border-line-strong disabled:opacity-50",
                  !project && "text-placeholder",
                )}
              >
                <option value="">生成后选择项目…</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy}
                onClick={() => void createProject()}
                title="新建项目"
                className="flex h-7 shrink-0 items-center gap-1 rounded-full border border-line bg-transparent px-2.5 text-[12px] text-secondary transition-colors hover:border-line-strong hover:text-primary disabled:opacity-50"
              >
                <Briefcase size={12} strokeWidth={1.9} /> 新建
              </button>
            </div>
          }
          right={
            <ComposerSendButton
              running={false}
              disabled={!canSubmit}
              onSend={onSubmit}
              onStop={() => {}}
              labels={{ send: "生成大纲" }}
            />
          }
        />
      </ComposerFrame>
      <p className="mt-2 text-center text-[12px] text-tertiary">
        生成内容与大纲由 AI 产出，正式使用前请核对
      </p>
      </div>
    </div>
  );
}
