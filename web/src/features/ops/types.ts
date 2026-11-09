export interface Bucket {
  runs: number;
  errors: number;
  tokens_in: number;
  tokens_out: number;
  llm_calls: number;
  cost: number;
  avg_ms: number;
}

export interface CostPayload {
  window: { days: number; since: string; runs: number };
  totals: Bucket;
  by_day: Array<Bucket & { day: string }>;
  by_model: Array<Bucket & { model: string; priced: boolean }>;
  by_member: Array<Bucket & { owner_id: string; name: string }>;
  by_kind: Array<Bucket & { kind: string }>;
}

export interface AlertKind {
  kind: string;
  label: string;
  unit: string;
  suggest: number;
}

export interface AlertChannel {
  type: string;
  url: string;
}

export interface AlertRule {
  id: string;
  name: string;
  kind: string;
  kind_label: string;
  threshold: number;
  window_hours: number;
  channels: AlertChannel[];
  cooldown_minutes: number;
  enabled: boolean;
  last_fired_at: string | null;
  created_at: string;
  owner_id: string;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name: string;
  kind: string;
  value: number;
  threshold: number;
  message: string;
  fired_at: string;
  delivered: boolean;
  delivery_error: string | null;
}

export interface CheckResult {
  checked: number;
  fired: AlertEvent[];
}
