export interface SkillFile {
  path: string;
  size: number;
}

export interface SkillVersion {
  version: string;
  name?: string;
  description?: string;
  system_prompt?: string;
  tools?: string[];
  commit?: string;
  saved_at: string;
  note?: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  tools: string[];
  builtin: boolean;
  enabled: boolean;
  workspace_id: string;
  source: "local" | "github" | "zip";
  source_url: string;
  repo: string;
  ref: string;
  commit: string;
  subpath: string;
  author: string;
  license: string;
  files: SkillFile[];
  installed_at: string;
  updated_at: string;
  version?: string;
  versions?: SkillVersion[];
}

export interface RepoInfo {
  repo: string;
  ref: string;
  commit: string;
  commit_short: string;
  description: string;
  stars: number;
  license: string;
  author: string;
  url: string;
}

export interface Candidate {
  path: string;
  name: string;
  description: string;
  file_count: number;
  size_bytes: number;
}

export interface Preview {
  repo: RepoInfo;
  candidates: Candidate[];
  selected: Candidate | null;
}

export interface MarketItem {
  key: string;
  source: string;
  name: string;
  full_name: string;
  repo: string;
  description: string;
  author: string;
  stars: number;
  topics: string[];
  license: string;
  url: string;
  install_url: string;
  installed: number;
  fallback?: boolean;
}

export interface MarketResponse {
  source: string;
  query: string;
  total: number;
  cached: boolean;
  rate_limited: boolean;
  note: string;
  errors: string[];
  items: MarketItem[];
}
