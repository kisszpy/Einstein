/**
 * AI PPT 制作页（/ppt-studio）：左栏会话列表 + 右侧对话流 + 底部输入区。
 *
 * 主流程（PRD）：描述需求 → analyze 流（取材/追问/大纲）→ 确认卡微调 →
 * generate 流（渲染 pptx）→ 结果卡（下载 / 打开放映级编辑器 / 重新生成）。
 * 恢复会话时用 listGenerations 重签票据刷新结果卡的下载地址（拍板结论 #6 的兜底）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, PanelLeft, Plus, RefreshCw, RotateCcw } from "lucide-react";
import { confirmDialog } from "@/lib/confirm";
import { cn } from "@/lib/utils";
import { listProjects } from "@/features/browser/api";
import type { ProjectInfo } from "@/features/browser/types";
import * as api from "./api";
import { useAnalyzeStream, useGenerateStream } from "./use-ppt-stream";
import { PhaseTrack } from "./components/PhaseTrack";
import { SessionSidebar } from "./components/SessionSidebar";
import { TemplatePicker } from "./components/TemplatePicker";
import { PptComposer, DEFAULT_PPT_EXPERT_ID } from "./components/PptComposer";
import { OutlineCard } from "./components/OutlineCard";
import { ResultCard } from "./components/ResultCard";
import { DeckDrawer } from "./components/DeckDrawer";
import type {
  DeckPayload,
  DesignCatalog,
  Outline,
  PptGeneration,
  PptMessage,
  PptSession,
  ThemePackSummary,
} from "./types";

export function PptStudioPage() {
  const [sessions, setSessions] = useState<PptSession[]>([]);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [session, setSession] = useState<PptSession | null>(null);
  const [messages, setMessages] = useState<PptMessage[]>([]);

  const [prompt, setPrompt] = useState("");
  const [expertId, setExpertId] = useState(DEFAULT_PPT_EXPERT_ID);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [themePackId, setThemePackId] = useState("");
  const [project, setProject] = useState("");
  const [packs, setPacks] = useState<ThemePackSummary[]>([]);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [designs, setDesigns] = useState<DesignCatalog | null>(null);

  // 确认卡的本地编辑副本：仅在用户改过最新大纲后非空
  const [editedOutline, setEditedOutline] = useState<Outline | null>(null);
  const [editedMessageId, setEditedMessageId] = useState<string | null>(null);
  const [savingOutline, setSavingOutline] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [drawerDeck, setDrawerDeck] = useState<DeckPayload | null>(null);

  const analyze = useAnalyzeStream();
  const generate = useGenerateStream();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const wasAnalyzing = useRef(false);

  // -------------------------------------------------------------- 数据加载

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      /* 未登录/后端未起：空列表 */
    }
  }, []);

  const loadDesigns = useCallback(async () => {
    try {
      setDesigns(await api.getDesigns());
    } catch {
      /* 后端未起：设计台用兜底取值域 */
    }
  }, []);

  /** 上传/删除模板后必须重取家族目录，否则新模板要刷新页面才进得了设计台。 */
  const handlePacksChange = useCallback(
    (next: ThemePackSummary[]) => {
      setPacks(next);
      void loadDesigns();
    },
    [loadDesigns],
  );

  useEffect(() => {
    void loadSessions();
    void listProjects().then(setProjects).catch(() => undefined);
    void api.listThemePacks().then(setPacks).catch(() => undefined);
    void loadDesigns();
  }, [loadSessions, loadDesigns]);

  /** 打开/刷新会话：messages + 用 generations 重签票据回填结果卡。 */
  const refreshSession = useCallback(async (sessionId: string) => {
    try {
      const [detail, generations] = await Promise.all([
        api.getSession(sessionId),
        api.listGenerations(sessionId).catch(() => [] as PptGeneration[]),
      ]);
      setSession(detail.session);
      const byGen = new Map(generations.map((g) => [g.generation_id, g]));
      setMessages(
        detail.messages.map((msg) => {
          const result = msg.payload.result;
          if (result) {
            const fresh = byGen.get(result.generation_id);
            if (fresh) return { ...msg, payload: { ...msg.payload, result: fresh } };
          }
          return msg;
        }),
      );
    } catch (err) {
      setActionError((err as Error).message);
    }
  }, []);

  const openSession = useCallback(
    async (target: PptSession) => {
      setActiveId(target.id);
      setEditedOutline(null);
      setEditedMessageId(null);
      setActionError(null);
      setDrawerDeck(null);
      // 会话自身的配置回填到输入区（拍板结论 #3：专家可换）
      setExpertId(target.expert_id || DEFAULT_PPT_EXPERT_ID);
      setSkillIds(target.skill_ids ?? []);
      setThemePackId(target.theme_pack_id ?? "");
      setProject(target.project ?? "");
      await refreshSession(target.id);
    },
    [refreshSession],
  );

  const newSession = useCallback(() => {
    setActiveId(null);
    setSession(null);
    setMessages([]);
    setEditedOutline(null);
    setEditedMessageId(null);
    setActionError(null);
    setExpertId(DEFAULT_PPT_EXPERT_ID);
    setSkillIds([]);
    setThemePackId("");
    setProject("");
  }, []);

  const deleteSession = useCallback(
    async (target: PptSession) => {
      const ok = await confirmDialog({
        title: "删除会话",
        message: `删除「${target.title}」？已生成的 pptx 保留在项目里。`,
        confirmLabel: "删除",
        danger: true,
      });
      if (!ok) return;
      await api.deleteSession(target.id);
      setSessions((prev) => prev.filter((s) => s.id !== target.id));
      if (target.id === activeId) newSession();
    },
    [activeId, newSession],
  );

  // -------------------------------------------------------------- 提交分析

  const submitPrompt = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || analyze.state.running || generate.state.running) return;
      setActionError(null);
      let sid = activeId;
      try {
        if (!sid) {
          const created = await api.createSession({
            title: trimmed.slice(0, 20),
            expert_id: expertId || undefined,
            skill_ids: skillIds.length ? skillIds : undefined,
            theme_pack_id: themePackId || undefined,
            project: project || undefined,
          });
          sid = created.id;
          setActiveId(sid);
          setSession(created);
          setSessions((prev) => [created, ...prev]);
          setMessages([]);
        }
        setMessages((prev) => [
          ...prev,
          {
            id: `local-${Date.now()}`,
            seq: prev.length,
            role: "user",
            content: trimmed,
            payload: {},
            created_at: new Date().toISOString(),
          },
        ]);
        setPrompt("");
        await analyze.run(sid, {
          prompt: trimmed,
          expert_id: expertId || undefined,
          skill_ids: skillIds.length ? skillIds : undefined,
          theme_pack_id: themePackId || undefined,
        });
      } catch (err) {
        setActionError((err as Error).message);
      }
    },
    [activeId, analyze, expertId, generate.state.running, project, skillIds, themePackId],
  );

  // analyze 结束后拉取服务端消息（大纲/追问消息由后端落库），保持单一事实来源
  useEffect(() => {
    if (wasAnalyzing.current && !analyze.state.running && analyze.state.sessionId) {
      if (!analyze.state.error) {
        void refreshSession(analyze.state.sessionId);
        void loadSessions();
      }
      setEditedOutline(null);
      setEditedMessageId(null);
    }
    wasAnalyzing.current = analyze.state.running;
  }, [analyze.state.running, analyze.state.sessionId, analyze.state.error, refreshSession, loadSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, analyze.state.deltaText, generate.state.running]);

  useEffect(() => {
    if (!sessionsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSessionsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sessionsOpen]);

  // -------------------------------------------------------------- 确认与生成

  const latestOutline = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const msg = messages[i];
      if (msg?.payload.outline) return msg;
    }
    return null;
  }, [messages]);

  const confirmGenerate = useCallback(async () => {
    if (!activeId) return;
    const outline = editedOutline ?? latestOutline?.payload.outline;
    if (!outline) return;
    setSavingOutline(true);
    setActionError(null);
    try {
      const saved = await api.saveOutline(activeId, outline, editedMessageId ?? undefined);
      setEditedOutline(null);
      setEditedMessageId(null);
      await generate.run(activeId, {
        outline: saved.outline,
        project: project || undefined,
      });
      await refreshSession(activeId);
      void loadSessions();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setSavingOutline(false);
    }
  }, [activeId, editedOutline, editedMessageId, latestOutline, generate, project, refreshSession, loadSessions]);

  // 会话是否正在流式处理（侧栏提示 + 输入区禁用）
  const busy = analyze.state.running || generate.state.running || savingOutline;

  // 快捷指令：更多页 / 更少页 / 换个风格（复用当前会话再跑一轮 analyze）
  const quickAction = useCallback(
    (text: string) => {
      if (busy || !activeId) return;
      void submitPrompt(text);
    },
    [busy, activeId, submitPrompt],
  );

  const openEditor = (generation: PptGeneration) => {
    if (generation.deck) setDrawerDeck(generation.deck);
  };

  // -------------------------------------------------------------- 消息渲染

  const renderMessage = (msg: PptMessage) => {
    if (msg.role === "user") {
      return (
        <div key={msg.id} className="flex justify-end">
          <div className="max-w-[70%] rounded-2xl rounded-br-md bg-brand-soft px-3.5 py-2 text-[13px] leading-6 text-brand-ink ">
            {msg.content}
          </div>
        </div>
      );
    }

    const outlinePayload = msg.payload.outline;
    const isEditable =
      outlinePayload && latestOutline?.id === msg.id && session?.status === "awaiting_confirm";

    if (outlinePayload) {
      if (isEditable) {
        const value = editedOutline ?? outlinePayload;
        return (
          <div key={msg.id} className="flex justify-start">
            <OutlineCard
              outline={value}
              onChange={(next) => {
                setEditedOutline(next);
                setEditedMessageId(msg.id);
              }}
              onConfirm={() => void confirmGenerate()}
              onSave={
                activeId
                  ? () => {
                      void (async () => {
                        if (!activeId) return;
                        setSavingOutline(true);
                        try {
                          await api.saveOutline(activeId, value, msg.id);
                          setEditedOutline(null);
                          setEditedMessageId(null);
                          await refreshSession(activeId);
                        } catch (err) {
                          setActionError((err as Error).message);
                        } finally {
                          setSavingOutline(false);
                        }
                      })();
                    }
                  : undefined
              }
              busy={busy}
              saveBusy={savingOutline}
              sessionId={activeId}
              designs={designs}
            />
          </div>
        );
      }
      return (
        <div key={msg.id} className="flex justify-start">
          <div className="w-full max-w-[720px] rounded-xl border border-line bg-surface-1 px-3.5 py-2.5 text-[12.5px] text-secondary">
            <span className="font-medium text-primary">{outlinePayload.title}</span>
            <span className="ml-2 text-tertiary">
              {outlinePayload.sections.length} 章 · {outlinePayload.page_count} 内容页
            </span>
          </div>
        </div>
      );
    }

    if (msg.payload.clarification) {
      const { question, options } = msg.payload.clarification;
      return (
        <div key={msg.id} className="flex justify-start">
          <div className="w-full max-w-[640px] rounded-xl border border-line bg-surface-1 p-3.5">
            <p className="text-[13px] text-primary">{question}</p>
            {options.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {options.map((option) => (
                  <button
                    key={option}
                    type="button"
                    disabled={busy}
                    onClick={() => quickAction(option)}
                    className="rounded-full border border-line px-2.5 py-1 text-[12px] text-secondary transition-colors hover:border-brand/40 hover:text-brand-ink disabled:opacity-50"
                  >
                    {option}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      );
    }

    if (msg.payload.result) {
      return (
        <div key={msg.id} className="flex justify-start">
          <ResultCard
            generation={msg.payload.result}
            onOpenEditor={openEditor}
            onRegenerate={latestOutline ? () => void confirmGenerate() : undefined}
            busy={busy}
          />
        </div>
      );
    }

    // 纯文本回复（澄清说明等）
    return (
      <div key={msg.id} className="flex justify-start">
        <div className="max-w-[640px] whitespace-pre-wrap text-[13px] leading-6 text-secondary">
          {msg.content}
        </div>
      </div>
    );
  };

  const quickActions = latestOutline
    ? ["再增加 2 页左右", "精简到更少页数", "换个风格重新规划"]
    : [];

  return (
    <div className="flex h-full min-h-0">
      {/* 窄屏把 264px 的会话栏收进抽屉：留着它正文只剩百来像素，标题会竖着排 */}
      <div
        className={cn(
          "shrink-0 lg:static lg:z-auto lg:flex",
          sessionsOpen ? "fixed inset-y-0 left-0 z-40 flex shadow-float" : "hidden",
        )}
      >
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeId}
          onSelect={(target) => {
            setSessionsOpen(false);
            void openSession(target);
          }}
          onDelete={(target) => void deleteSession(target)}
          onNew={() => {
            setSessionsOpen(false);
            newSession();
          }}
        />
      </div>
      {sessionsOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/45 lg:hidden"
          onClick={() => setSessionsOpen(false)}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col bg-surface-2">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2 lg:hidden">
          <button
            type="button"
            onClick={() => setSessionsOpen(true)}
            className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12.5px] text-secondary transition-colors hover:bg-hover"
          >
            <PanelLeft size={14} strokeWidth={1.9} /> 会话
          </button>
          <button
            type="button"
            onClick={newSession}
            className="flex items-center gap-1.5 rounded-lg bg-brand-soft px-2.5 py-1.5 text-[12.5px] font-medium text-brand-ink transition-colors hover:bg-brand/20"
          >
            <Plus size={14} strokeWidth={2} /> 新建
          </button>
          <span className="ml-auto min-w-0 truncate text-[12px] text-tertiary">
            {session?.title ?? "新演示"}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
          {messages.length === 0 && !analyze.state.running && (
            <div className="mx-auto mt-16 max-w-[560px] text-center">
              <h2 className="text-[17px] font-semibold text-primary">AI 制作演示文稿</h2>
              <p className="mt-2 text-[13px] leading-6 text-tertiary">
                描述主题、受众与页数偏好，助手会联网取材并规划大纲。
                确认卡里能直接拧设计旋钮，浏览器实测出真实页面预览；
                确认后写成原生可编辑的 pptx，可在线编辑与下载。
              </p>
              <div className="mt-4 flex items-center justify-center gap-2 text-[12px] text-tertiary">
              <span>设计风格：</span>
              <TemplatePicker
                value={themePackId}
                onChange={setThemePackId}
                packs={packs}
                onPacksChange={handlePacksChange}
              />
            </div>
            </div>
          )}

          <div className="mx-auto flex max-w-[820px] flex-col gap-4">
            {messages.map(renderMessage)}

            {/* analyze 流式面板 */}
            {analyze.state.running && (
              <PhaseTrack
                phase={analyze.state.phase}
                progress={analyze.state.progress}
                label={analyze.state.phaseLabel}
              />
            )}
            {analyze.state.deltaText && !analyze.state.outline && (
              <div className="whitespace-pre-wrap rounded-xl border border-line bg-surface-1 px-3.5 py-2.5 text-[13px] leading-6 text-secondary">
                {analyze.state.deltaText}
              </div>
            )}
            {analyze.state.clarification && !analyze.state.outline && (
              <div className="rounded-xl border border-line bg-surface-1 p-3.5">
                <p className="text-[13px] text-primary">{analyze.state.clarification.question}</p>
                {analyze.state.clarification.options.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {analyze.state.clarification.options.map((option) => (
                      <button
                        key={option}
                        type="button"
                        disabled={analyze.state.running}
                        onClick={() => void submitPrompt(option)}
                        className="rounded-full border border-line px-2.5 py-1 text-[12px] text-secondary transition-colors hover:border-brand/40 hover:text-brand-ink disabled:opacity-50"
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {analyze.state.error && (
              <div className="rounded-xl border border-danger/30 bg-danger/10 px-3.5 py-2.5">
                <p className="flex items-center gap-1.5 text-[12.5px] text-danger">
                  <AlertTriangle size={13} /> {analyze.state.error}
                </p>
                {analyze.state.retryable && activeId && (
                  <button
                    type="button"
                    onClick={() => void submitPrompt(prompt || "继续")}
                    className="mt-1.5 flex items-center gap-1 text-[12px] text-danger underline-offset-2 hover:underline"
                  >
                    <RotateCcw size={11} /> 重试
                  </button>
                )}
              </div>
            )}

            {/* generate 流式面板 */}
            {generate.state.running && (
              <PhaseTrack
                mode="generate"
                phase={generate.state.phase}
                progress={generate.state.progress}
                label={generate.state.phaseLabel || "正在渲染幻灯片…"}
              />
            )}
            {generate.state.error && (
              <div className="rounded-xl border border-danger/30 bg-danger/10 px-3.5 py-2.5">
                <p className="flex items-center gap-1.5 text-[12.5px] text-danger">
                  <AlertTriangle size={13} /> {generate.state.error}
                </p>
                {generate.state.retryable && latestOutline && (
                  <button
                    type="button"
                    onClick={() => void confirmGenerate()}
                    className="mt-1.5 flex items-center gap-1 text-[12px] text-danger underline-offset-2 hover:underline"
                  >
                    <RefreshCw size={11} /> 重新生成
                  </button>
                )}
              </div>
            )}
            {/* 流式结果卡：refreshSession 已把这条结果拉进消息流时就不再重复渲染 */}
            {generate.state.generation &&
              !generate.state.running &&
              !messages.some(
                (m) => m.payload.result?.generation_id === generate.state.generation?.generation_id,
              ) && (
                <ResultCard
                  generation={generate.state.generation}
                  onOpenEditor={openEditor}
                  onRegenerate={latestOutline ? () => void confirmGenerate() : undefined}
                  busy={busy}
                />
              )}

            {actionError && (
              <p className="text-center text-[12px] text-danger">{actionError}</p>
            )}

            {/* 快捷指令 */}
            {quickActions.length > 0 && !busy && (
              <div className="flex flex-wrap gap-1.5">
                {quickActions.map((action) => (
                  <button
                    key={action}
                    type="button"
                    onClick={() => quickAction(action)}
                    className="rounded-full border border-line bg-surface-1 px-3 py-1.5 text-[12px] text-tertiary transition-colors hover:border-brand/40 hover:text-brand-ink"
                  >
                    {action}
                  </button>
                ))}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <PptComposer
          prompt={prompt}
          onPromptChange={setPrompt}
          expertId={expertId}
          onExpertChange={setExpertId}
          skillIds={skillIds}
          onSkillIdsChange={setSkillIds}
          themePackId={themePackId}
          onThemePackChange={setThemePackId}
          packs={packs}
          onPacksChange={handlePacksChange}
          project={project}
          onProjectChange={setProject}
          projects={projects}
          onProjectsChange={setProjects}
          onSubmit={() => void submitPrompt(prompt)}
          busy={busy}
        />
      </div>

      <DeckDrawer
        deck={drawerDeck}
        onClose={() => setDrawerDeck(null)}
        onSaved={() => {
          // 编辑器写回成功：项目文件大小变了，重拉项目列表即可（产物页自行刷新）
          void listProjects().then(setProjects).catch(() => undefined);
        }}
      />
    </div>
  );
}
