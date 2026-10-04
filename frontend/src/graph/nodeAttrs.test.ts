import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { instanceAttrs } from './nodeAttrs';

function node(label: string): GraphData['nodes'][number] {
  return { id: `${label}:X`, label, key: 'X', title: label, props: {}, tweet_url: null };
}

describe('instanceAttrs', () => {
  it('stamps every node as a square glyph', () => {
    for (const label of ['Ticker', 'Sector', 'Committee', 'Politician', 'Insider', 'Event', 'SomethingNew']) {
      expect(instanceAttrs(node(label), new Map()).type).toBe('square');
    }
  });
});
