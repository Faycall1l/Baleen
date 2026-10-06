import type { SignalsBundle } from './types';

// Unified signal rows (S1): the five engine sections collapse into one ranked
// list. Scores are within-kind percentiles (0-100), so a top insider cluster
// and a top options print compare honestly without fake cross-kind math.
// Ties break toward the most actionable kinds first.

export type SignalKind = 'suspicious' | 'cluster' | 'momentum' | 'options' | 'uncovered';

export interface SignalRow {
  key: string;
  kind: SignalKind;
  /** 0-100: rank percentile within its kind. */
  score: number;
  headline: string;
  detail: string;
  /** Primary click target (opens the ticker node, else filters the tape). */
  ticker: string | null;
  url: string | null;
  /** Extra tappable tickers (uncovered sectors). */
  symbols: string[];
}

const KIND_ORDER: Record<SignalKind, number> = {
  suspicious: 0,
  cluster: 1,
  momentum: 2,
  options: 3,
  uncovered: 4,
};

const KIND_TAG: Record<SignalKind, string> = {
  suspicious: 'BEATS MARKET',
  cluster: 'INSIDER CLUSTER',
  momentum: 'CONGRESS FLOW',
  options: 'OPTIONS',
  uncovered: 'NO OVERSIGHT',
};

export function kindTag(kind: SignalKind): string {
  return KIND_TAG[kind];
}

interface Draft extends Omit<SignalRow, 'score'> {
  strength: number;
}

function percentile<T>(items: T[], strength: (item: T) => number): Map<T, number> {
  const ranked = [...items].sort((a, b) => strength(b) - strength(a));
  const out = new Map<T, number>();
  ranked.forEach((item, i) => {
    out.set(item, ranked.length <= 1 ? 100 : Math.round((100 * (ranked.length - 1 - i)) / (ranked.length - 1)));
  });
  return out;
}

export function toSignalRows(bundle: SignalsBundle | null, limit = 15): SignalRow[] {
  if (!bundle) return [];
  const drafts: Draft[] = [];
  const tickerOf = (v: unknown): string | null =>
    typeof v === 'string' && v !== '' ? v : null;
  for (const s of bundle.suspicious) {
    const ticker = tickerOf(s.ticker);
    if (!ticker) continue;
    drafts.push({
      key: `suspicious:${s.who}:${s.ticker}`,
      kind: 'suspicious',
      strength: Number(s.excess_pct) || 0,
      headline: `${s.who ?? 'A trader'} · ${s.ticker} +${Number(s.excess_pct).toFixed(1)}% vs market`,
      detail: 'Trade beating the market — check the filing.',
      ticker: ticker,
      url: s.source_url || null,
      symbols: [],
    });
  }
  for (const c of bundle.clusters) {
    const ticker = tickerOf(c.ticker);
    if (!ticker) continue;
    const insiders = Array.isArray(c.insiders)
      ? c.insiders.filter((w): w is string => typeof w === 'string' && w !== '')
      : [];
    const n = insiders.length;
    drafts.push({
      key: `cluster:${c.ticker}`,
      kind: 'cluster',
      strength: n * 1000 + (Number(c.buys) || 0),
      headline: `${n} insider${n === 1 ? '' : 's'} buying ${c.ticker}`,
      detail: insiders.slice(0, 3).join(', ') + (n > 3 ? ` +${n - 3} more` : ''),
      ticker: ticker,
      url: null,
      symbols: [],
    });
  }
  for (const m of bundle.momentum) {
    const ticker = tickerOf(m.ticker);
    if (!ticker) continue;
    const buys = Number(m.buys) || 0;
    const sells = Number(m.sells) || 0;
    const names = Array.isArray(m.names)
      ? m.names.filter((w): w is string => typeof w === 'string' && w !== '')
      : [];
    drafts.push({
      key: `momentum:${m.ticker}`,
      kind: 'momentum',
      strength: (buys + sells) * Math.max(1, Number(m.actors) || 0),
      headline: `${m.ticker} ${buys} buys / ${sells} sells`,
      detail: `${Number(m.actors) || 0} members trading${names.length ? `: ${names.slice(0, 3).join(', ')}` : ''}`,
      ticker: ticker,
      url: null,
      symbols: [],
    });
  }
  for (const o of bundle.options_meter) {
    const ticker = tickerOf(o.ticker);
    if (!ticker) continue;
    const calls = Number(o.call_vol) || 0;
    const puts = Number(o.put_vol) || 0;
    drafts.push({
      key: `options:${o.ticker}`,
      kind: 'options',
      strength: Number(o.prints) || 0,
      headline: `${o.ticker} ${Number(o.prints) || 0} unusual prints`,
      detail: `${calls.toLocaleString()} calls / ${puts.toLocaleString()} puts`,
      ticker: ticker,
      url: null,
      symbols: [],
    });
  }
  for (const u of bundle.uncovered) {
    const symbols = (u.symbols ?? []).filter((s): s is string => typeof s === 'string' && s !== '');
    drafts.push({
      key: `uncovered:${u.politician}:${u.sector}`,
      kind: 'uncovered',
      strength: symbols.length,
      headline: `${u.politician ?? 'A member'} × ${u.sector ?? 'an undisclosed sector'} unsupervised`,
      detail: 'Trading a sector with no committee oversight.',
      ticker: null,
      url: null,
      symbols,
    });
  }
  const byKind = new Map<SignalKind, Draft[]>();
  for (const d of drafts) {
    const list = byKind.get(d.kind) ?? [];
    list.push(d);
    byKind.set(d.kind, list);
  }
  const scores = new Map<Draft, number>();
  for (const list of byKind.values()) {
    const pct = percentile(list, (d) => d.strength);
    for (const d of list) scores.set(d, pct.get(d) ?? 0);
  }
  const rows: SignalRow[] = drafts.map((d) => ({
    key: d.key,
    kind: d.kind,
    score: scores.get(d) ?? 0,
    headline: d.headline,
    detail: d.detail,
    ticker: d.ticker,
    url: d.url,
    symbols: d.symbols,
  }));
  return rows
    .sort((a, b) => b.score - a.score || KIND_ORDER[a.kind] - KIND_ORDER[b.kind])
    .slice(0, limit);
}
