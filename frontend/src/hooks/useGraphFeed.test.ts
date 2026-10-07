import { describe, expect, it } from 'vitest';

import { BUSY_MAX_RETRIES, busyRetryDelay, isBusyError } from './useGraphFeed';

describe('busy-gate helpers', () => {
  it('recognizes shed-load responses by message', () => {
    expect(isBusyError(new Error('graph busy; retry shortly'))).toBe(true);
    expect(isBusyError(new Error('LLM endpoint returned 503: oops'))).toBe(true);
    expect(isBusyError(new Error('connection refused'))).toBe(false);
    expect(isBusyError('graph busy; retry shortly')).toBe(true);
    expect(isBusyError(null)).toBe(false);
  });

  it('backs off linearly and caps at the max retries', () => {
    expect(BUSY_MAX_RETRIES).toBe(3);
    expect(busyRetryDelay(1)).toBe(4000);
    expect(busyRetryDelay(2)).toBe(8000);
    expect(busyRetryDelay(3)).toBe(12000);
    expect(busyRetryDelay(99)).toBe(12000);
    expect(busyRetryDelay(0)).toBe(4000);
  });
});
