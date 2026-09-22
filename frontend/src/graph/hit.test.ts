import { describe, expect, it } from 'vitest';

import { hitNodeAt } from './hit';

const NODES = [
  { id: 'hub', x: 0, y: 0, r: 10 },
  { id: 'leaf', x: 7, y: 0, r: 3 },
  { id: 'far', x: 100, y: 100, r: 5 },
];

describe('hitNodeAt', () => {
  it('hits the node under the pointer', () => {
    expect(hitNodeAt(NODES, 100, 100)).toBe('far');
    expect(hitNodeAt(NODES, 200, 200)).toBeNull();
  });

  it('prefers the smallest containing circle', () => {
    // (5,0) is inside both hub (r10) and leaf (r3 centered at 7,0).
    expect(hitNodeAt(NODES, 5, 0)).toBe('leaf');
  });

  it('ignores non-finite candidates', () => {
    const bad = [...NODES, { id: 'ghost', x: NaN, y: 0, r: 50 }];
    expect(hitNodeAt(bad, 0, 0)).toBe('hub');
    expect(hitNodeAt([], 0, 0)).toBeNull();
  });
});
