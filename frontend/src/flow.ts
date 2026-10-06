import type { FlowFilters } from './types';

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
