import { describe, expect, it } from 'vitest';

import type { SignalsBundle } from './types';
import { toLeads } from './leads';

function bundle(over: Partial<SignalsBundle> = {}): SignalsBundle {
  return {
    window_days: 30,
    momentum: [],
    clusters: [],
    options_meter: [],
    uncovered: [],
    suspicious: [],
    ...over,
  };
}

describe('toLeads', () => {
  it('returns nothing for a null bundle', () => {
    expect(toLeads(null)).toEqual([]);
  });

  it('ranks oversight first, then beaters by excess, then congress flow', () => {
    const leads = toLeads(
      bundle({
        momentum: [{ ticker: 'AAA', buys: 30, sells: 0, actors: 9, names: [] }],
        suspicious: [
          { who: 'X', ticker: 'BBB', excess_pct: 5, source_url: '' },
          { who: 'Y', ticker: 'CCC', excess_pct: 60, source_url: 'http://f' },
        ],
        uncovered: [{ politician: 'Z', sector: 'Energy', symbols: ['XOM'] }],
      }),
    );
    expect(leads.map((l) => l.kind)).toEqual([
      'oversight',
      'beater',
      'beater',
      'congress-flow',
    ]);
    expect(leads[0].person).toBe('Z');
    expect(leads[0].symbols).toEqual(['XOM']);
    expect(leads[1].ticker).toBe('CCC');
    expect(leads[1].url).toBe('http://f');
  });

  it('skips trader-only kinds and null tickers', () => {
    const leads = toLeads(
      bundle({
        clusters: [{ ticker: 'INS', insiders: ['A'], buys: 9 }],
        options_meter: [{ ticker: 'OPT', call_vol: 9, put_vol: 1, prints: 9 }],
        momentum: [{ ticker: null as unknown as string, buys: 9, sells: 0, actors: 9, names: [] }],
      }),
    );
    expect(leads).toEqual([]);
  });

  it('falls back instead of printing null names', () => {
    const leads = toLeads(
      bundle({
        suspicious: [{ who: null as unknown as string, ticker: 'AAA', excess_pct: 9, source_url: '' }],
        uncovered: [{ politician: null as unknown as string, sector: null as unknown as string, symbols: [] }],
      }),
    );
    expect(leads[1].headline).toContain('A trader');
    expect(leads[0].headline).toContain('A member');
    expect(leads.every((l) => !l.headline.includes('null'))).toBe(true);
  });

  it('caps the list', () => {    const momentum = Array.from({ length: 20 }, (_, i) => ({
      ticker: `T${i}`,
      buys: 1,
      sells: 0,
      actors: 1,
      names: [] as string[],
    }));
    expect(toLeads(bundle({ momentum }), 5)).toHaveLength(5);
  });
});
