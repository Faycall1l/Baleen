import { describe, expect, it } from 'vitest';

import { hashStr } from './geometry';

describe('hashStr', () => {
  it('is deterministic and bounded', () => {
    expect(hashStr('abc')).toBe(hashStr('abc'));
    expect(hashStr('abc')).not.toBe(hashStr('abd'));
    expect(hashStr('x')).toBeGreaterThanOrEqual(0);
    expect(hashStr('x')).toBeLessThan(1);
  });
});
