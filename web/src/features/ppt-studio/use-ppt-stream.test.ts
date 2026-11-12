import { describe, expect, it } from "vitest";

import {
  emptyAnalyzeState,
  emptyGenerateState,
  reduceAnalyze,
  reduceGenerate,
} from "./use-ppt-stream";
import type { Outline } from "./types";
import { DEFAULT_KNOBS } from "./design";

/**
 * SSE 状态机是前端契约的核心：确认卡覆盖 delta 文本、error 中断累计、
 * 生成流的 result/done 顺序——这些规则错了，用户会看到卡内容闪烁或报错后继续吐字。
 */

const outline: Outline = {
  title: "季度经营复盘",
  subtitle: "",
  audience: "",
  lang: "zh",
  page_count: 3,
  style: { ...DEFAULT_KNOBS, theme_pack_id: "", preset_theme: "", tone: "" },
  sources_policy: "off",
  version: 1,
  sections: [
    {
      id: "sec_1",
      title: "正文",
      pages: [
        { id: "pg_1", title: "开场", kicker: "", layout: "cover", bullets: [], note: "", sources: [], layout_resolved: "" },
        { id: "pg_2", title: "核心", kicker: "", layout: "bullets", bullets: ["a"], note: "", sources: [], layout_resolved: "" },
        { id: "pg_3", title: "收尾", kicker: "", layout: "end", bullets: [], note: "", sources: [], layout_resolved: "" },
      ],
    },
  ],
  sources: [],
};

describe("reduceAnalyze", () => {
  it("按 meta → phase → web → sources → delta 顺序累积状态", () => {
    let s = reduceAnalyze(emptyAnalyzeState, "meta", { session_id: "ps_1" });
    expect(s.sessionId).toBe("ps_1");
    s = reduceAnalyze(s, "phase", { phase: "collecting", label: "正在联网检索", progress: 10 });
    expect(s.phase).toBe("collecting");
    expect(s.progress).toBe(10);
    s = reduceAnalyze(s, "web", { status: "searching", query: "复盘" });
    expect(s.web?.status).toBe("searching");
    s = reduceAnalyze(s, "sources", { sources: [{ id: "w1", kind: "web", title: "T", url: "u", kb_name: "", snippet: "" }] });
    expect(s.sources).toHaveLength(1);
    s = reduceAnalyze(s, "delta", { text: "正在" });
    s = reduceAnalyze(s, "delta", { text: "规划大纲" });
    expect(s.deltaText).toBe("正在规划大纲");
  });

  it("outline 事件覆盖 delta 文本并携带 message_id", () => {
    let s = reduceAnalyze(emptyAnalyzeState, "delta", { text: "部分草稿" });
    s = reduceAnalyze(s, "outline", { outline, message_id: "pm_1" });
    expect(s.deltaText).toBe("");
    expect(s.outline?.title).toBe("季度经营复盘");
    expect(s.outlineMessageId).toBe("pm_1");
    expect(s.clarification).toBeNull();
  });

  it("clarification 事件给出追问与选项", () => {
    const s = reduceAnalyze(emptyAnalyzeState, "clarification", {
      question: "受众是谁？",
      options: ["管理层"],
      message_id: "pm_2",
    });
    expect(s.clarification?.question).toBe("受众是谁？");
    expect(s.clarification?.options).toEqual(["管理层"]);
    expect(s.outline).toBeNull();
  });

  it("error 置错误并停止 delta 累计；done 结束 running", () => {
    let s = reduceAnalyze(emptyAnalyzeState, "delta", { text: "前半段" });
    s = reduceAnalyze(s, "error", { message: "解析失败", retryable: true });
    expect(s.error).toBe("解析失败");
    expect(s.retryable).toBe(true);
    const frozen = reduceAnalyze(s, "delta", { text: "不应出现" });
    expect(frozen.deltaText).toBe("前半段");
    const done = reduceAnalyze(s, "done", {});
    expect(done.running).toBe(false);
  });
});

describe("reduceGenerate", () => {
  const generation = {
    generation_id: "gen_1",
    project: "demo",
    deck_path: "deck/复盘.pptx",
    total_slides: 4,
    layouts_used: ["Title Slide"],
    fidelity: { score: 100, issues: [], degradations: [], summary_zh: "4 页生成完成" },
    status: "ok",
    error: "",
    created_at: "2026-11-11T00:00:00",
    deck: { project: "demo", path: "deck/复盘.pptx", name: "复盘.pptx", bytes: 1, ticket: "t", download_url: "/x" },
  };

  it("phase → result → done 顺序成立，result 带可打开的 deck", () => {
    let s = reduceGenerate(emptyGenerateState, "phase", { phase: "rendering", label: "生成中", progress: 40 });
    expect(s.phase).toBe("rendering");
    s = reduceGenerate(s, "result", { generation });
    expect(s.generation?.deck?.path).toBe("deck/复盘.pptx");
    s = reduceGenerate(s, "done", { generation_id: "gen_1" });
    expect(s.running).toBe(false);
    expect(s.progress).toBe(100);
    expect(s.generation).not.toBeNull();
  });

  it("error 置 retryable；无 result 时 generation 保持 null", () => {
    const s = reduceGenerate(emptyGenerateState, "error", { message: "模板源丢失", retryable: false });
    expect(s.error).toBe("模板源丢失");
    expect(s.retryable).toBe(false);
    expect(s.generation).toBeNull();
  });
});
