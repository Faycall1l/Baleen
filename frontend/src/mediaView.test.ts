import { describe, expect, it } from 'vitest';

import { fmtAge } from './mediaView';

const NOW = Date.parse('2026-10-31T12:00:00Z');

describe('media age labels', () => {
  it('reads relative for recent items', () => {
    expect(fmtAge('2026-10-31T11:59:40Z', NOW)).toBe('just now');
    expect(fmtAge('2026-10-31T11:45:00Z', NOW)).toBe('15m ago');
    expect(fmtAge('2026-10-31T09:00:00Z', NOW)).toBe('3h ago');
  });

  it('falls back to a date for older items and handles garbage', () => {
    expect(fmtAge('2026-10-20T12:00:00Z', NOW)).toContain('2026');
    expect(fmtAge('not a date', NOW)).toBe('—');
  });
});
