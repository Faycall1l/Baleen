import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { centerOnCentroid, connectedComponents, forceLayout, iterationsForOrder, liftIsolates, normalizeRadius, packComponents, withFiniteFallback } from './forceLayout';

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

describe('connectedComponents', () => {
  it('splits pairs, chains, and isolates deterministically, largest first', () => {
    const data: GraphData = {
      nodes: [node('Ticker:A'), node('Ticker:B'), node('Ticker:C'), node('Ticker:D'), node('Ticker:X')],
      links: [
        { source: 'Ticker:A', target: 'Ticker:B', type: 'T' },
        { source: 'Ticker:C', target: 'Ticker:D', type: 'T' },
      ],
      node_count: 5,
      link_count: 2,
    };
    expect(connectedComponents(data)).toEqual([
      ['Ticker:A', 'Ticker:B'],
      ['Ticker:C', 'Ticker:D'],
      ['Ticker:X'],
    ]);
    // Stable across calls and independent of link order.
    const flipped: GraphData = { ...data, links: [...data.links].reverse() };
    expect(connectedComponents(flipped)).toEqual(connectedComponents(data));
    expect(connectedComponents({ nodes: [], links: [], node_count: 0, link_count: 0 })).toEqual([]);
  });
});

describe('packComponents', () => {
  it('separates disconnected groups and leaves single components alone', () => {
    const two: GraphData = {
      nodes: [node('Ticker:A'), node('Ticker:B'), node('Ticker:C'), node('Ticker:D')],
      links: [
        { source: 'Ticker:A', target: 'Ticker:B', type: 'T' },
        { source: 'Ticker:C', target: 'Ticker:D', type: 'T' },
      ],
      node_count: 4,
      link_count: 2,
    };
    const seeds = new Map([
      ['Ticker:A', { x: 0, y: 0 }],
      ['Ticker:B', { x: 1, y: 1 }],
      ['Ticker:C', { x: 2, y: 2 }],
      ['Ticker:D', { x: 3, y: 3 }],
    ]);
    const packed = packComponents(seeds, two);
    const centroid = (ids: string[]) => {
      const pts = ids.map((id) => packed.get(id)!);
      return {
        x: pts.reduce((s, p) => s + p.x, 0) / pts.length,
        y: pts.reduce((s, p) => s + p.y, 0) / pts.length,
      };
    };
    const ab = centroid(['Ticker:A', 'Ticker:B']);
    const cd = centroid(['Ticker:C', 'Ticker:D']);
    const between = Math.hypot(ab.x - cd.x, ab.y - cd.y);
    const within = Math.max(
      Math.hypot(ab.x - packed.get('Ticker:A')!.x, ab.y - packed.get('Ticker:A')!.y),
      Math.hypot(cd.x - packed.get('Ticker:C')!.x, cd.y - packed.get('Ticker:C')!.y),
    );
    expect(between).toBeGreaterThan(within);
    // Single component: identical map back.
    const one: GraphData = { ...two, links: two.links.slice(0, 1) };
    expect(packComponents(seeds, one)).toEqual(seeds);
  });
});

describe('normalizeRadius', () => {
  it('scales the extent to the target, preserving direction', () => {
    const out = normalizeRadius(
      new Map([
        ['a', { x: 6, y: 8 }],
        ['b', { x: -3, y: -4 }],
      ]),
      20,
    );
    expect(out.get('a')).toEqual({ x: 12, y: 16 });
    expect(out.get('b')).toEqual({ x: -6, y: -8 });
    expect(normalizeRadius(new Map(), 20).size).toBe(0);
    expect(normalizeRadius(new Map([['z', { x: 0, y: 0 }]]), 20).get('z')).toEqual({ x: 0, y: 0 });
  });
});

describe('centerOnCentroid', () => {
  it('moves the centroid to the origin, preserving relative layout', () => {
    const positions = centerOnCentroid(
      new Map([
        ['a', { x: 10, y: 10 }],
        ['b', { x: 20, y: 30 }],
      ]),
    );
    expect(positions.get('a')).toEqual({ x: -5, y: -10 });
    expect(positions.get('b')).toEqual({ x: 5, y: 10 });
    expect(centerOnCentroid(new Map()).size).toBe(0);
  });
});

describe('liftIsolates', () => {
  it('rings zero-degree nodes outside the connected mass', () => {
    const star: GraphData = {
      nodes: [node('Ticker:C'), node('Ticker:L1'), node('Ticker:L2'), node('Ticker:X')],
      links: [
        { source: 'Ticker:C', target: 'Ticker:L1', type: 'T' },
        { source: 'Ticker:C', target: 'Ticker:L2', type: 'T' },
      ],
      node_count: 4,
      link_count: 2,
    };
    const positions = forceLayout(star);
    const halo = positions.get('Ticker:X')!;
    const haloR = Math.hypot(halo.x, halo.y);
    const connected = ['Ticker:C', 'Ticker:L1', 'Ticker:L2'].map((id) => {
      const p = positions.get(id)!;
      return Math.hypot(p.x, p.y);
    });
    expect(haloR).toBeGreaterThan(Math.max(...connected));
    // Deterministic: same input, same halo slot.
    expect(forceLayout(star).get('Ticker:X')).toEqual(halo);
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

  it('lets live positions steer the result (staged upgrades keep the map)', () => {
    // Seeds feed the engine: an asymmetric graph started from flung live
    // spots must settle somewhere different than from radial seeds. (Exact
    // geometry is the engine's business; influence is what we own.)
    const star: GraphData = {
      nodes: [node('Ticker:C'), node('Ticker:L1'), node('Ticker:L2'), node('Ticker:L3')],
      links: [
        { source: 'Ticker:C', target: 'Ticker:L1', type: 'T' },
        { source: 'Ticker:C', target: 'Ticker:L2', type: 'T' },
        { source: 'Ticker:C', target: 'Ticker:L3', type: 'T' },
      ],
      node_count: 4,
      link_count: 3,
    };
    const plain = forceLayout(star);
    const shifted = forceLayout(
      star,
      new Map([
        ['Ticker:C', { x: 0, y: 0 }],
        ['Ticker:L1', { x: 5000, y: 5000 }],
        ['Ticker:L2', { x: -5000, y: 5000 }],
        ['Ticker:L3', { x: 0, y: -5000 }],
      ]),
    );
    expect([...shifted.entries()]).not.toEqual([...plain.entries()]);
    for (const { x, y } of shifted.values()) {
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
  });

  it('ignores existing spots for unknown nodes and stays deterministic', () => {
    const once = forceLayout(PAIR, new Map([['Ticker:Ghost', { x: 1, y: 1 }]]));
    const twice = forceLayout(PAIR, new Map([['Ticker:Ghost', { x: 1, y: 1 }]]));
    expect([...once.entries()]).toEqual([...twice.entries()]);
    expect(once.size).toBe(2);
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
