import { describe, expect, it } from 'vitest';

import { formatCell, isUrl, visibleRows } from './queryTable';

describe('answer table helpers', () => {
  it('detects source URLs for link rendering', () => {
    expect(isUrl('https://x.com/unusual_whales/status/1')).toBe(true);
    expect(isUrl('http://localhost:8000/health')).toBe(true);
    expect(isUrl('NVDA')).toBe(false);
    expect(isUrl(42)).toBe(false);
    expect(isUrl(null)).toBe(false);
  });

  it('formats cells without shifting meaning', () => {
    expect(formatCell(null)).toBe('—');
    expect(formatCell(undefined)).toBe('—');
    expect(formatCell(true)).toBe('TRUE');
    expect(formatCell(false)).toBe('FALSE');
    expect(formatCell(5000)).toBe('5,000');
    expect(formatCell('NVDA')).toBe('NVDA');
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
  });

  it('caps rendered rows so a 200-row bounded result stays light', () => {
    const rows = Array.from({ length: 120 }, (_, i) => ({ n: i }));
    expect(visibleRows(rows)).toHaveLength(50);
    expect(visibleRows([{ n: 1 }])).toEqual([{ n: 1 }]);
  });
});
