/**
 * analyze / generate 两条 SSE 流的状态机。
 *
 * reducer 导出为纯函数（`reduceAnalyze` / `reduceGenerate`），单测直接喂帧
 * 断言状态转移；hook 只负责 fetch + readSseStream + AbortController。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { readSseStream } from "@/features/chat/sse-parser";
import * as api from "./api";
import type {
  AnalyzeStreamState,
  GenerateStreamState,
  Outline,
  PptGeneration,
  SourceRef,
} from "./types";

export const emptyAnalyzeState: AnalyzeStreamState = {
  running: false,
  sessionId: null,
  phase: null,
  progress: 0,
  phaseLabel: "",
  web: null,
  sources: [],
  deltaText: "",
  outline: null,
  outlineMessageId: null,
  clarification: null,
  error: null,
  retryable: false,
};

export const emptyGenerateState: GenerateStreamState = {
  running: false,
  phase: null,
  progress: 0,
  phaseLabel: "",
  generation: null,
  error: null,
  retryable: false,
};

interface PhaseFrame {
  phase?: string;
  label?: string;
  progress?: number;
}

/** analyze 事件 → 状态。error 之后的 delta 不再累计（中断语义）。 */
export function reduceAnalyze(
  state: AnalyzeStreamState,
  event: string,
  raw: unknown,
): AnalyzeStreamState {
  switch (event) {
    case "meta":
      return { ...state, sessionId: (raw as { session_id: string }).session_id ?? state.sessionId };
    case "phase": {
      const frame = raw as PhaseFrame;
      return {
        ...state,
        phase: (frame.phase as AnalyzeStreamState["phase"]) ?? state.phase,
        progress: frame.progress ?? state.progress,
        phaseLabel: frame.label ?? state.phaseLabel,
      };
    }
    case "web":
      return { ...state, web: raw as AnalyzeStreamState["web"] };
    case "sources": {
      const { sources } = raw as { sources: SourceRef[] };
      return { ...state, sources: sources ?? [] };
    }
    case "delta": {
      if (state.error) return state;
      const { text } = raw as { text: string };
      return { ...state, deltaText: state.deltaText + text };
    }
    case "clarification": {
      const data = raw as { question: string; options: string[]; message_id?: string };
      return {
        ...state,
        clarification: { question: data.question, options: data.options ?? [] },
        outlineMessageId: data.message_id ?? state.outlineMessageId,
      };
    }
    case "outline": {
      const data = raw as { outline: Outline; message_id: string };
      return {
        ...state,
        outline: data.outline,
        outlineMessageId: data.message_id,
        clarification: null,
        deltaText: "",
      };
    }
    case "error": {
      const data = raw as { message: string; retryable?: boolean };
      return { ...state, error: data.message, retryable: Boolean(data.retryable) };
    }
    case "done":
      return { ...state, running: false, progress: 100 };
    default:
      return state;
  }
}

/** generate 事件 → 状态。 */
export function reduceGenerate(
  state: GenerateStreamState,
  event: string,
  raw: unknown,
): GenerateStreamState {
  switch (event) {
    case "phase": {
      const frame = raw as PhaseFrame;
      return {
        ...state,
        phase: (frame.phase as GenerateStreamState["phase"]) ?? state.phase,
        progress: frame.progress ?? state.progress,
        phaseLabel: frame.label ?? state.phaseLabel,
      };
    }
    case "result": {
      const { generation } = raw as { generation: PptGeneration };
      return { ...state, generation };
    }
    case "error": {
      const data = raw as { message: string; retryable?: boolean };
      return { ...state, error: data.message, retryable: Boolean(data.retryable) };
    }
    case "done":
      return { ...state, running: false, progress: 100 };
    default:
      return state;
  }
}

/** analyze 流 hook：返回状态 + run/stop。 */
export function useAnalyzeStream() {
  const [state, setState] = useState<AnalyzeStreamState>(emptyAnalyzeState);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async (sessionId: string, input: api.AnalyzeInput) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ ...emptyAnalyzeState, running: true, sessionId });
    try {
      const res = await api.analyzeRequest(sessionId, input, controller.signal);
      if (!res.ok || !res.body) {
        const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
        throw new Error(detail || `HTTP ${res.status}`);
      }
      await readSseStream(res.body, (event, raw) =>
        setState((prev) => reduceAnalyze(prev, event, raw)),
      );
      setState((prev) => ({ ...prev, running: false }));
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setState((prev) => ({ ...prev, running: false }));
        return;
      }
      setState((prev) => ({
        ...prev,
        running: false,
        error: (err as Error).message,
        retryable: true,
      }));
    } finally {
      abortRef.current = null;
    }
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setState(emptyAnalyzeState), []);
  return { state, run, stop, reset };
}

/** generate 流 hook。 */
export function useGenerateStream() {
  const [state, setState] = useState<GenerateStreamState>(emptyGenerateState);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async (sessionId: string, input: api.GenerateInput) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ ...emptyGenerateState, running: true });
    try {
      const res = await api.generateRequest(sessionId, input, controller.signal);
      if (!res.ok || !res.body) {
        const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
        throw new Error(detail || `HTTP ${res.status}`);
      }
      await readSseStream(res.body, (event, raw) =>
        setState((prev) => reduceGenerate(prev, event, raw)),
      );
      setState((prev) => ({ ...prev, running: false }));
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setState((prev) => ({ ...prev, running: false }));
        return;
      }
      setState((prev) => ({
        ...prev,
        running: false,
        error: (err as Error).message,
        retryable: true,
      }));
    } finally {
      abortRef.current = null;
    }
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setState(emptyGenerateState), []);
  return { state, run, stop, reset };
}
