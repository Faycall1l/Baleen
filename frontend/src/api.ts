// Thin client for the Baleen API surfaces.

import { buildFlowQuery } from './flow';
import type { Alert, CommitteeCard, FlowFilters, FlowResponse, GraphData, Health, LeaderboardEntry, PersonCard, QueryResponse, Report, SignalsBundle, TickerCard } from './types';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// 150 recent events keeps the payload + canvas interactive; the header
// stats line says when the view is truncated. Pass `since` (ISO) for a
// delta: only newer events, merged client-side into the snapshot. Pass
// `before` (ISO) for an as-of snapshot: events at/before that time.
export function fetchGraph(
  limit = 150,
  signal?: AbortSignal,
  since?: string,
  before?: string,
  withoutEvents = false,
): Promise<GraphData> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (since != null && since !== '') params.set('since', since);
  if (before != null && before !== '') params.set('before', before);
  if (withoutEvents) params.set('without_events', 'true');
  return json<GraphData>(`/api/graph?${params.toString()}`, signal ? { signal } : undefined);
}

export function askQuestion(question: string): Promise<QueryResponse> {
  return json<QueryResponse>('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }).then((res) => ({
    ...res,
    columns: asArray(res.columns),
    rows: asArray(res.rows),
    followups: asArray(res.followups),
    sources: asArray(res.sources),
  }));
}

export function fetchAlerts(limit = 20): Promise<{ alerts: Alert[]; count: number }> {
  return json<{ alerts: Alert[]; count: number }>(`/api/alerts?limit=${limit}`).then((res) => ({
    ...res,
    alerts: asArray(res.alerts),
  }));
}

export async function fetchHealth(): Promise<Health | null> {
  // Lenient on purpose: a degraded backend answers 503 with a body naming
  // the failing check, and the pill should read "degraded" — not "offline".
  // Only an unreachable backend resolves to null.
  let res: Response;
  try {
    res = await fetch('/health');
  } catch {
    return null;
  }
  try {
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export function fetchReports(limit = 20): Promise<{ reports: Report[]; count: number }> {
  return json<{ reports: Report[]; count: number }>(`/api/reports?limit=${limit}`).then((res) => ({
    ...res,
    reports: asArray(res.reports),
  }));
}

export function fetchDigest(): Promise<{ report: Report }> {
  return json<{ report: Report }>('/api/reports/digest');
}

export function fetchLeaderboard(limit = 10): Promise<{ leaders: LeaderboardEntry[]; count: number }> {
  return json<{ leaders: LeaderboardEntry[]; count: number }>(`/api/leaderboard?limit=${limit}`).then(
    (res) => ({ ...res, leaders: asArray(res.leaders) }),
  );
}

export interface AlertRule {
  name: string;
  active: boolean;
}

export function fetchRules(): Promise<{ rules: AlertRule[]; count: number }> {
  return json<{ rules: AlertRule[]; count: number }>('/api/rules').then((res) => ({
    ...res,
    rules: asArray(res.rules),
  }));
}

export function setRuleActive(name: string, active: boolean): Promise<AlertRule> {
  return json<AlertRule>(`/api/rules/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active }),
  });
}

export function fetchSignals(): Promise<SignalsBundle> {
  return json<SignalsBundle>('/api/signals?window_days=90').then((res) => ({
    ...res,
    momentum: asArray(res.momentum),
    clusters: asArray(res.clusters),
    options_meter: asArray(res.options_meter),
    uncovered: asArray(res.uncovered),
    suspicious: asArray(res.suspicious),
  }));
}

export function fetchFlow(filters: FlowFilters, signal?: AbortSignal): Promise<FlowResponse> {
  return json<FlowResponse>(buildFlowQuery(filters), signal ? { signal } : undefined).then((res) => ({
    ...res,
    flow: asArray(res.flow),
  }));
}

function asArray<T>(v: T[] | null | undefined): T[] {
  return Array.isArray(v) ? v : [];
}

export function fetchPerson(politicianId: string): Promise<{ person: PersonCard }> {
  return json<{ person: PersonCard }>(`/api/politicians/${encodeURIComponent(politicianId)}`).then(
    (res) => ({
      person: {
        ...res.person,
        committees: asArray(res.person?.committees),
        trades: asArray(res.person?.trades),
      },
    }),
  );
}

export function fetchTicker(symbol: string): Promise<{ ticker: TickerCard }> {
  return json<{ ticker: TickerCard }>(`/api/tickers/${encodeURIComponent(symbol)}`).then((res) => ({
    ticker: {
      ...res.ticker,
      trades: asArray(res.ticker?.trades),
      flow: asArray(res.ticker?.flow),
    },
  }));
}

export function fetchCommittee(committeeId: string): Promise<{ committee: CommitteeCard }> {
  return json<{ committee: CommitteeCard }>(`/api/committees/${encodeURIComponent(committeeId)}`).then(
    (res) => ({
      committee: {
        ...res.committee,
        sectors: asArray(res.committee?.sectors),
        members: asArray(res.committee?.members),
        trades: asArray(res.committee?.trades),
      },
    }),
  );
}

export interface PathNode {
  id: string;
  label: string;
  title: string;
}

export interface PathResponse {
  from: string;
  to: string;
  nodes: PathNode[];
  rels: string[];
  length: number;
}

export function fetchPath(fromId: string, toId: string): Promise<PathResponse> {
  const params = new URLSearchParams({ from: fromId, to: toId });
  return json<PathResponse>(`/api/graph/path?${params.toString()}`);
}
