import { describe, expect, it } from 'vitest';

import { matchAlerts, personIdForNode } from './personView';
import type { Alert, PersonTrade } from './types';

function trade(event_id: string): PersonTrade {
  return { event_id, ticker: 'NVDA', side: 'sell', date: '2026-09-01', amount: null, source_url: null };
}

function alert(id: number, event_id: string): Alert {
  return {
    id,
    rule_name: 'politician_trades_in_oversight_sector',
    event_id,
    tweet_id: `t${id}`,
    tweet_url: 'https://x.com/y',
    title: 'title',
    summary: 'summary',
    graph_refs: [],
    created_at: '2026-09-29T00:00:00Z',
  };
}

describe('person view', () => {
  it('joins trades to alerts by event id, keeping unalerted trades', () => {
    const joined = matchAlerts([trade('e1'), trade('e2')], [alert(1, 'e1'), alert(2, 'e1')]);
    expect(joined[0].alerts.map((a) => a.id)).toEqual([1, 2]);
    expect(joined[1].alerts).toEqual([]);
  });

  it('resolves the politician id from props, falling back to the node key', () => {
    expect(personIdForNode('Politician', 'Politician:W000817', { id: 'W000817' })).toBe('W000817');
    expect(personIdForNode('Politician', 'Politician:W000817', {})).toBe('W000817');
    expect(personIdForNode('Ticker', 'Ticker:NVDA', { id: 'NVDA' })).toBeNull();
    expect(personIdForNode('Politician', 'Politician:', {})).toBeNull();
  });
});
