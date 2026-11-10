/** 知识库工作台的领域类型与小工具。
 *
 * 单独成文件的原因：这些类型同时被页面本体、文档列表、检索台三处消费，
 * 一旦留在 KbPage.tsx 里就会形成「组件 → 组件」的类型导入，拆子组件时
 * 只能靠 re-export 打补丁。
 */

export interface KBItem {
  id: string;
  name: string;
  description: string;
  visibility: string;
  owner_id: string;
  mine: boolean;
  document_count: number;
  my_role: "read" | "write";
}

export interface DocItem {
  id: string;
  name: string;
  chunk_count: number;
  version: number;
  source: string;
  status: string;
  updated_at: string;
  source_url: string;
  fetched_at: string;
  stale: boolean;
}

export interface ChunkItem {
  id: string;
  seq: number;
  text: string;
  chars: number;
  char_start: number;
  char_end: number;
  embedding_kind: string;
  has_vector: boolean;
  parent_seq: number | null;
}

export interface DocVersion {
  version: number;
  chunk_count: number;
  chars: number;
  source: string;
  note: string;
  created_at: string;
}

export interface SearchHit {
  chunk_id: string;
  document_name: string;
  text: string;
  score: number;
  vector_score: number | null;
  lexical_score: number | null;
}

export type SearchMode = "hybrid" | "vector" | "lexical";

/** 对比视图按固定顺序渲染，不用 Object.keys(结果) —— 后端字段顺序不保证。 */
export const SEARCH_MODES: SearchMode[] = ["hybrid", "vector", "lexical"];

export const MODE_LABEL: Record<SearchMode, string> = {
  hybrid: "混合",
  vector: "向量",
  lexical: "词法",
};

export interface CompareResult {
  query: string;
  results: Partial<Record<SearchMode, SearchHit[]>>;
  timings_ms: Record<string, number>;
}

export function shortTime(iso: string): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}
