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
