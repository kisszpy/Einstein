export type ChatRole = "user" | "assistant";

/** 联网模式：自动按需 / 本轮必查 / 本轮不查 */
export type WebMode = "auto" | "on" | "off";

export interface WebSource {
  index: number;
  title: string;
  url: string;
  snippet: string;
  fetched: boolean;
  /** 抓取正文后回填的真实地址（重定向后可能与 url 不同） */
  final_url?: string;
}

/** 一轮对话里联网的进展与结果 */
export interface WebState {
  status: "checking" | "searching" | "done" | "skipped" | "failed";
  mode?: WebMode;
  query?: string;
  reason?: string;
  error?: string;
  message?: string;
  elapsed_ms?: number;
  sources?: WebSource[];
}

export interface Citation {
  index: number;
  document_name: string;
  chunk_id: string;
  text: string;
  score: number;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** 正在流式接收中 */
  streaming?: boolean;
  /** 请求失败信息 */
  error?: string;
  /** 知识库引用（RAG 命中） */
  citations?: Citation[];
  /** 思考过程（Qwen3 reasoning_content） */
  reasoning?: string;
  /** 本轮联网状态与来源 */
  web?: WebState;
}

/** 后端 SSE 事件 */
export type StreamEvent =
  | { event: "meta"; data: { conversation_id: string } }
  | { event: "delta"; data: { text: string } }
  | { event: "reasoning"; data: { text: string } }
  | { event: "web"; data: WebState }
  | { event: "citations"; data: { citations: Citation[] } }
  | { event: "done"; data: { conversation_id: string } }
  | { event: "error"; data: { message: string } };
