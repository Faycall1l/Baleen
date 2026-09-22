import { describe, expect, it } from 'vitest';

import { computeInitialLayout, placeMissing } from './layout';

const IDS = ['a', 'b', 'c', 'd'];
const EDGES: [string, string][] = [
  ['a', 'b'],
  ['b', 'c'],
];

describe('computeInitialLayout', () => {
  it('places every id with finite coordinates', () => {
    const pos = computeInitialLayout(IDS, EDGES, 50);
    expect([...pos.keys()].sort()).toEqual([...IDS].sort());
    for (const p of pos.values()) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });

  it('is deterministic for the same input', () => {
    const first = computeInitialLayout(IDS, EDGES, 50);
    const second = computeInitialLayout(IDS, EDGES, 50);
    expect([...second.entries()]).toEqual([...first.entries()]);
  });

  it('handles empty input', () => {
    expect(computeInitialLayout([], [], 10).size).toBe(0);
  });
});

describe('placeMissing', () => {
  it('keeps existing spots and seeds the rest', () => {
    const have = new Map([['a', { x: 10, y: 10 }]]);
    const adj = new Map([
      ['a', ['b']],
      ['b', ['a', 'c']],
      ['c', ['b']],
    ]);
    const out = placeMissing(['a', 'b', 'c'], adj, have);
    expect(out.get('a')).toEqual({ x: 10, y: 10 });
    // b sits on its placed neighbor; c cascades onto b in the same pass.
    expect(out.get('b')).toEqual({ x: 10, y: 10 });
    expect(out.get('c')).toEqual({ x: 10, y: 10 });
    const rerun = placeMissing(['a', 'b', 'c'], adj, have);
    expect([...rerun.entries()]).toEqual([...out.entries()]);
  });
});
