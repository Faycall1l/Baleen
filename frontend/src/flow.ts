import type { FlowFilters, FlowPrint } from './types';

export const FLOW_DEFAULTS: FlowFilters = {
  tickers: [],
  side: 'all',
  min_ratio: 2,
  window_days: 30,
  limit: 50,
};

export function buildFlowQuery(filters: FlowFilters): string {
  const params = new URLSearchParams();
  if (filters.tickers.length > 0) {
    params.set('tickers', filters.tickers.join(','));
  }
  params.set('side', filters.side);
  params.set('min_ratio', String(filters.min_ratio));
  params.set('window_days', String(filters.window_days));
  params.set('limit', String(filters.limit));
  return `/api/signals/flow?${params.toString()}`;
}

export function formatRatio(ratio: number | null | undefined): string {
  return ratio == null || Number.isNaN(ratio) ? '—' : `${ratio.toFixed(1)}x`;
}

export function formatContracts(value: number | null | undefined): string {
  return value == null ? '—' : value.toLocaleString('en-US');
}

export function formatStrike(strike: number | null | undefined): string {
  return strike == null ? '—' : `$${strike.toLocaleString('en-US')}`;
}

export function formatExpiry(expiry: string | null | undefined): string {
  return expiry?.slice(0, 10) ?? '—';
}

export function formatTapeTime(value: string | null | undefined): string {
  if (!value) return '—';
  return value.slice(0, 16).replace('T', ' ');
}

export function sentimentClass(sentiment: string | null | undefined): 'up' | 'down' | 'flat' {
  if (sentiment === 'bullish') return 'up';
  if (sentiment === 'bearish') return 'down';
  return 'flat';
}

const CSV_COLUMNS: Array<{ key: keyof FlowPrint; label: string }> = [
  { key: 'event_id', label: 'event_id' },
  { key: 'ticker', label: 'ticker' },
  { key: 'side', label: 'side' },
  { key: 'strike', label: 'strike' },
  { key: 'expiration', label: 'expiration' },
  { key: 'premium', label: 'premium' },
  { key: 'volume', label: 'volume' },
  { key: 'open_interest', label: 'open_interest' },
  { key: 'ratio', label: 'ratio' },
  { key: 'sentiment', label: 'sentiment' },
  { key: 'event_time', label: 'event_time' },
  { key: 'first_seen_at', label: 'first_seen_at' },
  { key: 'source_url', label: 'source_url' },
];

function csvCell(value: unknown): string {
  if (value == null) return '';
  const s = String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Tape rows as CSV for data desks: exactly what the filters show, raw
 * values (no display formatting) so spreadsheets stay computable. */
export function printsToCSV(flow: FlowPrint[]): string {
  const head = CSV_COLUMNS.map((c) => c.label).join(',');
  const rows = flow.map((print) =>
    CSV_COLUMNS.map((c) => csvCell(print[c.key])).join(','),
  );
  return [head, ...rows].join('\n');
}

export function flowFilename(when: Date = new Date()): string {
  return `baleen-flow-${when.toISOString().slice(0, 10)}.csv`;
}

export function downloadCSV(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
