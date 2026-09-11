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
  event_count?: number;
  truncated?: boolean;
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

export interface HealthCheck {
  status: string;
  detail?: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  app: string;
  checks: Record<string, HealthCheck>;
}
