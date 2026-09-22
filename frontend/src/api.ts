// Thin client for the Baleen API surfaces.

import type { Alert, GraphData, Health, LeaderboardEntry, QueryResponse, Report, SignalsBundle } from './types';

// Fire-and-forget client diagnostics: lifecycle milestones and caught graph
// failures POST here so they show up in backend logs without anyone opening
// the browser console. Never throws, never blocks rendering.
const reportedWindows = new Set<string>();

export function reportUx(event: string, detail = ''): void {
  try {
    if (event === 'window-error') {
      if (reportedWindows.has(detail)) return;
      reportedWindows.add(detail);
    }
    void fetch('/api/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, detail: detail.slice(0, 2000) }),
    }).catch(() => {});
  } catch {
    /* diagnostics must never break the app */
  }
}

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
// delta: only newer events, merged client-side into the snapshot.
export function fetchGraph(limit = 150, signal?: AbortSignal, since?: string): Promise<GraphData> {
  const url =
    since != null && since !== ''
      ? `/api/graph?limit=${limit}&since=${encodeURIComponent(since)}`
      : `/api/graph?limit=${limit}`;
  return json<GraphData>(url, signal ? { signal } : undefined);
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

export function fetchSignals(): Promise<SignalsBundle> {
  return json<SignalsBundle>('/api/signals?window_days=90');
}
