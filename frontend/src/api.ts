// Thin client for the three Baleen API surfaces.

import type { Alert, GraphData, QueryResponse } from './types';

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

export function fetchGraph(): Promise<GraphData> {
  return json<GraphData>('/api/graph');
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
