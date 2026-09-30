import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { forceLayout, iterationsForOrder, withFiniteFallback } from './forceLayout';

function node(id: string, label = 'Ticker', title = id): GraphData['nodes'][number] {
  return { id, label, key: id, title, props: {}, tweet_url: null };
}

const PAIR: GraphData = {
  nodes: [node('Ticker:A'), node('Ticker:B')],
  links: [{ source: 'Ticker:A', target: 'Ticker:B', type: 'INVOLVES_TICKER' }],
  node_count: 2,
  link_count: 1,
};

describe('iterationsForOrder', () => {
  it('holds full iterations for small graphs and floors at the minimum', () => {
    expect(iterationsForOrder(0)).toBe(100);
    expect(iterationsForOrder(300)).toBe(100);
    expect(iterationsForOrder(600)).toBe(50);
    expect(iterationsForOrder(1000)).toBe(30);
    expect(iterationsForOrder(100000)).toBe(30);
  });
});

describe('withFiniteFallback', () => {
  it('replaces non-finite engine output with radial seeds', () => {
    const seeds = new Map([
      ['a', { x: 1, y: 2 }],
      ['b', { x: 3, y: 4 }],
    ]);
    const positions = new Map([
      ['a', { x: 10, y: 20 }],
      ['b', { x: NaN, y: Infinity }],
    ]);
    const out = withFiniteFallback(positions, seeds);
    expect(out.get('a')).toEqual({ x: 10, y: 20 });
    expect(out.get('b')).toEqual({ x: 3, y: 4 });
  });
});

describe('forceLayout', () => {
  it('places every node at finite coordinates', () => {
    const positions = forceLayout(PAIR);
    expect(positions.size).toBe(2);
    for (const { x, y } of positions.values()) {
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
  });

  it('pulls connected nodes closer than the radial seeds', () => {
    const positions = forceLayout(PAIR);
    const a = positions.get('Ticker:A')!;
    const b = positions.get('Ticker:B')!;
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    // Radial seeds park them on opposite-ish ring slots; FA2 must contract.
    expect(dist).toBeLessThan(200);
  });

  it('is deterministic for the same payload', () => {
    const first = forceLayout(PAIR);
    const second = forceLayout(PAIR);
    expect([...first.entries()]).toEqual([...second.entries()]);
  });

  it('returns radial seeds untouched for huge graphs', () => {
    const nodes = Array.from({ length: 1300 }, (_, i) => node(`Ticker:N${i}`));
    const data: GraphData = { nodes, links: [], node_count: 1300, link_count: 0 };
    const positions = forceLayout(data);
    expect(positions.size).toBe(1300);
  });

  it('handles empty input', () => {
    expect(forceLayout({ nodes: [], links: [], node_count: 0, link_count: 0 }).size).toBe(0);
  });

  it('places even a single edgeless node at finite coordinates', () => {
    const solo: GraphData = {
      nodes: [node('Ticker:Solo')],
      links: [],
      node_count: 1,
      link_count: 0,
    };
    const positions = forceLayout(solo);
    expect(positions.size).toBe(1);
    const p = positions.get('Ticker:Solo')!;
    expect(Number.isFinite(p.x)).toBe(true);
    expect(Number.isFinite(p.y)).toBe(true);
  });
});
