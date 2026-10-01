import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { instanceAttrs, nodeShape } from './nodeAttrs';

function node(label: string): GraphData['nodes'][number] {
  return { id: `${label}:X`, label, key: 'X', title: label, props: {}, tweet_url: null };
}

describe('nodeShape', () => {
  it('renders entities as squares and events as discs', () => {
    for (const label of ['Ticker', 'Sector', 'Committee', 'Politician', 'Insider']) {
      expect(nodeShape(label)).toBe('square');
    }
    expect(nodeShape('Event')).toBe('bordered');
    expect(nodeShape('SomethingNew')).toBe('square');
  });

  it('stamps the program type onto instance attrs', () => {
    expect(instanceAttrs(node('Ticker'), new Map()).type).toBe('square');
    expect(instanceAttrs(node('Event'), new Map()).type).toBe('bordered');
  });
});
