import { describe, expect, it } from 'vitest';

import { SHORTCUTS } from './shortcuts';

describe('shortcuts', () => {
  it('documents every terminal shortcut with keys and an action', () => {
    expect(SHORTCUTS.length).toBeGreaterThan(0);
    for (const s of SHORTCUTS) {
      expect(s.keys.length).toBeGreaterThan(0);
      expect(s.action.length).toBeGreaterThan(0);
    }
  });
});
