// Shared API types mirroring the FastAPI responses in backend/app/api/routes.py.

export interface GraphNode {
  id: string;
  label: string;
  key: string;
  title: string;
  props: Record<string, unknown>;
  tweet_url?: string | null;
}

export interface GraphLink {
  source: string;
  target: string;
  type: string;
  tweet_url?: string | null;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
  node_count: number;
  link_count: number;
  event_count?: number | null;
  truncated?: boolean;
  delta?: boolean;
}

export interface QueryResponse {
  question: string;
  cypher: string;
  allowed: boolean;
  violations: string[];
  rows: unknown[];
  answer: string;
  sources: string[];
  has_data: boolean;
}

export interface Alert {
  id: number;
  rule_name: string;
  event_id: string;
  tweet_id: string;
  tweet_url: string;
  title: string;
  summary: string;
  graph_refs: string[];
  created_at: string;
}

export interface Report {
  id: number;
  kind: 'brief' | 'digest';
  event_id?: string | null;
  period?: string | null;
  title: string;
  body: string;
  graph_refs?: string[];
  created_at?: string;
}

export interface LeaderboardEntry {
  politician: string;
  ticker: string;
  side: string;
  date: string;
  amount: string;
  ret_pct: number;
  excess_pct: number | null;
  source_url: string;
}

export interface MomentumRow {
  ticker: string;
  buys: number;
  sells: number;
  actors: number;
  names?: string[] | null;
}

export interface ClusterRow {
  ticker: string;
  insiders: string[];
  buys: number;
}

export interface OptionsMeterRow {
  ticker: string;
  call_vol?: number | null;
  put_vol?: number | null;
  prints: number;
}

export interface SuspiciousRow {
  who: string;
  ticker: string;
  excess_pct: number;
  source_url: string;
}

export interface UncoveredRow {
  politician: string;
  sector: string;
  symbols?: string[] | null;
}

export interface SignalsBundle {
  window_days: number;
  momentum: MomentumRow[];
  clusters: ClusterRow[];
  options_meter: OptionsMeterRow[];
  uncovered: UncoveredRow[];
  suspicious: SuspiciousRow[];
}

export interface HealthCheck {
  status: string;
  detail?: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  app: string;
  checks: Record<string, HealthCheck>;
}
