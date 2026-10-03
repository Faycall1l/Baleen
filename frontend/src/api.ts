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
): Promise<GraphData> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (since != null && since !== '') params.set('since', since);
  if (before != null && before !== '') params.set('before', before);
  return json<GraphData>(`/api/graph?${params.toString()}`, signal ? { signal } : undefined);
}

export function askQuestion(question: string): Promise<QueryResponse> {
  return json<QueryResponse>('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
}

export function fetchAlerts(limit = 20): Promise<{ alerts: Alert[]; count: number }> {
  return json<{ alerts: Alert[]; count: number }>(`/api/alerts?limit=${limit}`);
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
  return json<{ reports: Report[]; count: number }>(`/api/reports?limit=${limit}`);
}

export function fetchDigest(): Promise<{ report: Report }> {
  return json<{ report: Report }>('/api/reports/digest');
}

export function fetchLeaderboard(limit = 10): Promise<{ leaders: LeaderboardEntry[]; count: number }> {
  return json<{ leaders: LeaderboardEntry[]; count: number }>(`/api/leaderboard?limit=${limit}`);
}

export interface AlertRule {
  name: string;
  active: boolean;
}

export function fetchRules(): Promise<{ rules: AlertRule[]; count: number }> {
  return json<{ rules: AlertRule[]; count: number }>('/api/rules');
}

export function setRuleActive(name: string, active: boolean): Promise<AlertRule> {
  return json<AlertRule>(`/api/rules/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active }),
  });
}

export function fetchSignals(): Promise<SignalsBundle> {
  return json<SignalsBundle>('/api/signals?window_days=90');
}

export function fetchFlow(filters: FlowFilters, signal?: AbortSignal): Promise<FlowResponse> {
  return json<FlowResponse>(buildFlowQuery(filters), signal ? { signal } : undefined);
}

export function fetchPerson(politicianId: string): Promise<{ person: PersonCard }> {
  return json<{ person: PersonCard }>(`/api/politicians/${encodeURIComponent(politicianId)}`);
}

export function fetchTicker(symbol: string): Promise<{ ticker: TickerCard }> {
  return json<{ ticker: TickerCard }>(`/api/tickers/${encodeURIComponent(symbol)}`);
}

export function fetchCommittee(committeeId: string): Promise<{ committee: CommitteeCard }> {
  return json<{ committee: CommitteeCard }>(`/api/committees/${encodeURIComponent(committeeId)}`);
}
