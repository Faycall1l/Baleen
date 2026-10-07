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

export interface QueryColumn {
  key: string;
  label: string;
  kind: 'string' | 'number' | 'boolean' | 'value' | 'unknown';
}

export interface QueryResponse {
  question: string;
  cypher: string;
  allowed: boolean;
  violations: string[];
  rows: Record<string, unknown>[];
  columns: QueryColumn[];
  followups: string[];
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
  ticker: string | null;
  buys: number;
  sells: number;
  actors: number;
  names?: Array<string | null> | null;
}

export interface ClusterRow {
  ticker: string | null;
  insiders: Array<string | null>;
  buys: number;
}

export interface OptionsMeterRow {
  ticker: string | null;
  call_vol?: number | null;
  put_vol?: number | null;
  prints: number;
}

export interface SuspiciousRow {
  who: string | null;
  ticker: string | null;
  excess_pct: number;
  source_url: string | null;
}

export type FlowSide = 'all' | 'call' | 'put';

export interface FlowPrint {
  event_id: string;
  ticker: string | null;
  side: 'call' | 'put' | null;
  strike: number | null;
  expiration: string | null;
  premium: number | null;
  volume: number | null;
  open_interest: number | null;
  ratio: number | null;
  sentiment: 'bullish' | 'bearish' | 'neutral' | null;
  event_time: string | null;
  first_seen_at: string | null;
  source_url: string | null;
}

export interface FlowFilters {
  tickers: string[];
  side: FlowSide;
  min_ratio: number;
  window_days: number;
  limit: number;
}

export interface FlowResponse {
  flow: FlowPrint[];
  count: number;
  filters: FlowFilters;
}

export interface PersonCommittee {
  id: string | null;
  name: string | null;
}

export interface PersonTrade {
  event_id: string;
  ticker: string | null;
  side: string | null;
  date: string | null;
  amount: string | null;
  source_url: string | null;
}

export interface PersonCard {
  id: string;
  name: string;
  chamber: string | null;
  party: string | null;
  committees: PersonCommittee[];
  trades: PersonTrade[];
}

export interface TickerFlowPrint {
  event_id: string;
  side: string | null;
  strike: number | null;
  expiration: string | null;
  volume: number | null;
  open_interest: number | null;
  ratio: number | null;
  sentiment: string | null;
}

export interface TickerTrade {
  event_id: string;
  kind: string | null;
  who: string | null;
  side: string | null;
  date: string | null;
  amount: string | null;
  source_url: string | null;
}

export interface TickerCard {
  ticker: string;
  sector: string | null;
  trades: TickerTrade[];
  flow: TickerFlowPrint[];
}

export interface CommitteeMember {
  id: string;
  name: string | null;
  chamber: string | null;
  party: string | null;
}

export interface CommitteeTrade {
  event_id: string;
  who: string | null;
  politician_id: string | null;
  ticker: string | null;
  side: string | null;
  date: string | null;
  amount: string | null;
  source_url: string | null;
}

export interface CommitteeCard {
  id: string;
  name: string | null;
  sectors: string[];
  members: CommitteeMember[];
  trades: CommitteeTrade[];
}

export interface UncoveredRow {
  politician: string | null;
  sector: string | null;
  symbols?: Array<string | null> | null;
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
