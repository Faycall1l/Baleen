import { describe, expect, it } from 'vitest';

import {
  FLOW_DEFAULTS,
  buildFlowQuery,
  formatContracts,
  formatExpiry,
  formatRatio,
  formatStrike,
  formatTapeTime,
  sentimentClass,
} from './flow';

describe('options tape helpers', () => {
  it('builds a stable, encoded tape query', () => {
    expect(buildFlowQuery(FLOW_DEFAULTS)).toBe(
      '/api/signals/flow?side=all&min_ratio=2&window_days=30&limit=50',
    );
    expect(
      buildFlowQuery({
        ...FLOW_DEFAULTS,
        tickers: ['NVDA', 'BRK B'],
        side: 'put',
        min_ratio: 3.5,
      }),
    ).toBe(
      '/api/signals/flow?tickers=NVDA%2CBRK+B&side=put&min_ratio=3.5&window_days=30&limit=50',
    );
  });

  it('formats terminal numerics without shifting meaning', () => {
    expect(formatRatio(5)).toBe('5.0x');
    expect(formatRatio(null)).toBe('—');
    expect(formatRatio(undefined)).toBe('—');
    expect(formatRatio(NaN)).toBe('—');
    expect(formatContracts(5000)).toBe('5,000');
    expect(formatContracts(null)).toBe('—');
    expect(formatStrike(130)).toBe('$130');
    expect(formatExpiry('2026-10-17')).toBe('2026-10-17');
    expect(formatTapeTime('2026-09-29T11:35:00+00:00')).toBe('2026-09-29 11:35');
    expect(sentimentClass('bullish')).toBe('up');
    expect(sentimentClass('bearish')).toBe('down');
    expect(sentimentClass('neutral')).toBe('flat');
  });
});

describe('printsToCSV', () => {
  it('exports raw values with quoting and blanks', async () => {
    const { printsToCSV, flowFilename } = await import('./flow');
    const csv = printsToCSV([
      {
        event_id: 'e1', ticker: 'NVDA', side: 'call', strike: 130, expiration: '2026-10-17',
        premium: 2.5, volume: 1200, open_interest: 1000, ratio: 1.2, sentiment: 'bullish',
        event_time: '2026-10-01T00:00:00Z', first_seen_at: '2026-10-02T00:00:00Z',
        source_url: 'https://x.com/y,"q"',
      },
      {
        event_id: 'e2', ticker: null, side: null, strike: null, expiration: null,
        premium: null, volume: null, open_interest: null, ratio: null, sentiment: null,
        event_time: null, first_seen_at: null, source_url: null,
      },
    ]);
    const [head, r1, r2] = csv.split('\n');
    expect(head).toBe(
      'event_id,ticker,side,strike,expiration,premium,volume,open_interest,ratio,sentiment,event_time,first_seen_at,source_url',
    );
    expect(r1).toContain('NVDA,call,130');
    expect(r1).toContain('"https://x.com/y,""q"""');
    expect(r2).toBe('e2,,,,,,,,,,,,');
    expect(flowFilename(new Date('2026-10-07T12:00:00Z'))).toBe('baleen-flow-2026-10-07.csv');
  });
});
