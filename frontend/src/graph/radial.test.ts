import { describe, expect, it } from 'vitest';

import { radialLayout, radialRadius } from './radial';
import type { GraphData } from '../types';

const tiny: GraphData = {
  nodes: [
    { id: 'e1', label: 'Event', key: 'e1', title: 'E1', props: {}, tweet_url: null },
    { id: 'e2', label: 'Event', key: 'e2', title: 'E2', props: {}, tweet_url: null },
    { id: 't1', label: 'Ticker', key: 'NVDA', title: 'NVDA', props: {}, tweet_url: null },
  ],
  links: [{ source: 'e1', target: 't1', type: 'INVOLVES_TICKER', tweet_url: null }],
  node_count: 3,
  link_count: 1,
  event_count: 2,
  truncated: false,
  delta: false,
};

describe('radial layout', () => {
  it('is deterministic for the same input', () => {
    const a = radialLayout(tiny);
    const b = radialLayout(tiny);
    expect([...a.entries()]).toEqual([...b.entries()]);
  });

  it('separates labels into different angular sectors', () => {
    const pos = radialLayout(tiny);
    const angleOf = (id: string) => {
      const p = pos.get(id)!;
      return Math.atan2(p.y, p.x);
    };
    // Same-label nodes share a sector (adjacent angles), other labels differ.
    const sameGap = Math.abs(angleOf('e1') - angleOf('e2'));
    const crossGap = Math.abs(angleOf('e1') - angleOf('t1'));
    expect(Math.min(sameGap, 2 * Math.PI - sameGap)).toBeLessThan(
      Math.min(crossGap, 2 * Math.PI - crossGap),
    );
  });

  it('bounds every node within the radius scale', () => {
    const pos = radialLayout(tiny);
    const R = radialRadius(tiny.nodes.length);
    for (const { x, y } of pos.values()) {
      expect(Math.hypot(x, y)).toBeLessThanOrEqual(R + 1e-9);
    }
  });

  it('handles empty input', () => {
    expect(radialLayout({ ...tiny, nodes: [], links: [] }).size).toBe(0);
  });
});
