import type { SignalsBundle } from './types';

// Story leads (J1): the signals bundle reframed for an investigative
// journalist. Only congress-relevant kinds make the cut — insider clusters
// and raw options volume stay in the Signals tile for traders. Leads read
// like story pitches: person, action, and why it matters, ranked with the
// most distinctive (unsupervised trading) first.

export type LeadKind = 'oversight' | 'beater' | 'congress-flow';

export interface Lead {
  key: string;
  kind: LeadKind;
  headline: string;
  dek: string;
  ticker: string | null;
  person: string | null;
  url: string | null;
  symbols: string[];
}

export const LEAD_TAG: Record<LeadKind, string> = {
  oversight: 'NO OVERSIGHT',
  beater: 'BEATS MARKET',
  'congress-flow': 'CONGRESS FLOW',
};

function tickerOf(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null;
}

/** Ranked story leads, strongest first. Empty bundle reads as no leads. */
export function toLeads(bundle: SignalsBundle | null, limit = 12): Lead[] {
  if (!bundle) return [];
  const scored: Array<{ lead: Lead; rank: number }> = [];
  for (const u of bundle.uncovered) {
    const symbols = (u.symbols ?? []).filter(
      (s): s is string => typeof s === 'string' && s !== '',
    );
    scored.push({
      lead: {
        key: `oversight:${u.politician}:${u.sector}`,
        kind: 'oversight',
        headline: `${u.politician} traded ${u.sector} with no committee watching`,
        dek:
          symbols.length > 0
            ? `Symbols: ${symbols.slice(0, 4).join(', ')}${symbols.length > 4 ? ` +${symbols.length - 4} more` : ''}`
            : 'Disclosed trades in a sector no committee oversees for them.',
        ticker: null,
        person: u.politician || null,
        url: null,
        symbols,
      },
      rank: 1000 + symbols.length,
    });
  }
  for (const s of bundle.suspicious) {
    const ticker = tickerOf(s.ticker);
    if (!ticker) continue;
    const excess = Number(s.excess_pct) || 0;
    scored.push({
      lead: {
        key: `beater:${s.who}:${s.ticker}`,
        kind: 'beater',
        headline: `${s.who} beat the market by ${excess.toFixed(1)}% on ${ticker}`,
        dek: 'A trade outperforming the market badly enough to ask questions about.',
        ticker,
        person: s.who || null,
        url: s.source_url || null,
        symbols: [],
      },
      rank: 500 + excess,
    });
  }
  for (const m of bundle.momentum) {
    const ticker = tickerOf(m.ticker);
    if (!ticker) continue;
    const buys = Number(m.buys) || 0;
    const sells = Number(m.sells) || 0;
    const actors = Number(m.actors) || 0;
    scored.push({
      lead: {
        key: `congress-flow:${m.ticker}`,
        kind: 'congress-flow',
        headline: `${ticker}: ${buys} buys / ${sells} sells across ${actors} members`,
        dek:
          m.names?.length
            ? `Names: ${m.names.slice(0, 3).join(', ')}${m.names.length > 3 ? ` +${m.names.length - 3} more` : ''}`
            : 'Unusual congressional volume in one ticker.',
        ticker,
        person: null,
        url: null,
        symbols: [],
      },
      rank: Math.min(499, (buys + sells) * Math.max(1, actors)),
    });
  }
  return scored
    .sort((a, b) => b.rank - a.rank)
    .slice(0, limit)
    .map(({ lead }) => lead);
}
