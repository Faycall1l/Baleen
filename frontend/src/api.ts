// Thin client for the Baleen API surfaces.

import type { Alert, GraphData, Health, QueryResponse, Report } from './types';

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

export function fetchGraph(limit = 500): Promise<GraphData> {
  return json<GraphData>(`/api/graph?limit=${limit}`);
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

export function fetchHealth(): Promise<Health> {
  return json<Health>('/health');
}

export function fetchReports(limit = 20): Promise<{ reports: Report[]; count: number }> {
  return json<{ reports: Report[]; count: number }>(`/api/reports?limit=${limit}`);
}

export function fetchDigest(): Promise<{ report: Report }> {
  return json<{ report: Report }>('/api/reports/digest');
}
