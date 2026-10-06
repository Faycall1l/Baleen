import { describe, expect, it } from 'vitest';

import type { SignalsBundle } from './types';
import { kindTag, toSignalRows } from './signalsView';

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

describe('toSignalRows', () => {
  it('returns nothing for a null bundle', () => {
    expect(toSignalRows(null)).toEqual([]);
  });

  it('ranks the strongest of each kind at 100 and orders ties by kind', () => {
    const rows = toSignalRows(
      bundle({
        suspicious: [
          { who: 'A', ticker: 'AAA', excess_pct: 5, source_url: '' },
          { who: 'B', ticker: 'BBB', excess_pct: 50, source_url: 'http://x' },
        ],
        clusters: [{ ticker: 'CCC', insiders: ['X', 'Y'], buys: 3 }],
        momentum: [{ ticker: 'DDD', buys: 2, sells: 1, actors: 2, names: ['Z'] }],
      }),
    );
    expect(rows.map((r) => r.key)).toEqual([
      'suspicious:B:BBB',
      'cluster:CCC',
      'momentum:DDD',
      'suspicious:A:AAA',
    ]);
    expect(rows[0].score).toBe(100);
    expect(rows[0].url).toBe('http://x');
    expect(rows[3].score).toBe(0);
    expect(rows[1].ticker).toBe('CCC');
  });

  it('caps rows and labels uncovered symbols', () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      ticker: `T${i}`,
      call_vol: 1,
      put_vol: 1,
      prints: i + 1,
    }));
    const rows = toSignalRows(
      bundle({
        options_meter: many,
        uncovered: [{ politician: 'P', sector: 'Energy', symbols: ['XOM', 'CVX'] }],
      }),
      5,
    );
    expect(rows).toHaveLength(5);
    expect(rows[0].kind).toBe('options');
    const unc = toSignalRows(
      bundle({ uncovered: [{ politician: 'P', sector: 'Energy', symbols: ['XOM'] }] }),
    );
    expect(unc[0].ticker).toBeNull();
    expect(unc[0].symbols).toEqual(['XOM']);
  });

  it('tags every kind', () => {
    for (const kind of ['suspicious', 'cluster', 'momentum', 'options', 'uncovered'] as const) {
      expect(kindTag(kind).length).toBeGreaterThan(0);
    }
  });
});
