import { describe, expect, it } from 'vitest';

import { daysAgoToISO, frameLabel, SCRUB_DAYS } from './timeScrub';

describe('timeScrub', () => {
  it('converts day offsets to ISO upper bounds', () => {
    const now = Date.parse('2026-09-30T12:00:00Z');
    expect(daysAgoToISO(0, now)).toBe('2026-09-30T12:00:00.000Z');
    expect(daysAgoToISO(9, now)).toBe('2026-09-21T12:00:00.000Z');
    expect(SCRUB_DAYS).toBe(30);
  });

  it('labels live and frozen frames', () => {
    expect(frameLabel(null)).toBe('LIVE');
    expect(frameLabel('2026-09-21T12:00:00.000Z')).toBe('frozen 2026-09-21');
  });
});
