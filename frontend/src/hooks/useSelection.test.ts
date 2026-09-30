import { describe, expect, it } from 'vitest';

import { isGhostReleaseDue, PICK_GRACE_MS } from './useSelection';

describe('isGhostReleaseDue', () => {
  it('never releases a node present in the graph', () => {
    expect(isGhostReleaseDue(0, Number.MAX_SAFE_INTEGER, true)).toBe(false);
  });

  it('spares fresh picks so the snapshot can catch up with the feed', () => {
    const now = 1_000_000;
    expect(isGhostReleaseDue(now - 1000, now, false)).toBe(false);
    expect(isGhostReleaseDue(now - PICK_GRACE_MS, now, false)).toBe(true);
    expect(isGhostReleaseDue(now - PICK_GRACE_MS - 1, now, false)).toBe(true);
  });
});
