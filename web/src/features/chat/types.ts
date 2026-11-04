export type ChatRole = "user" | "assistant";

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
}

/** 后端 SSE 事件 */
export type StreamEvent =
  | { event: "meta"; data: { conversation_id: string } }
  | { event: "delta"; data: { text: string } }
  | { event: "citations"; data: { citations: Citation[] } }
  | { event: "done"; data: { conversation_id: string } }
  | { event: "error"; data: { message: string } };
