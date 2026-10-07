import { describe, expect, it } from 'vitest';

import { resolveAlertNode } from './alertNavigation';
import type { Alert, GraphData } from './types';

function eventNode(id: string, propsId: string): GraphData['nodes'][number] {
  return { id, label: 'Event', key: id, title: 'NVDA call', props: { id: propsId }, tweet_url: null };
}

function tickerNode(ticker: string): GraphData['nodes'][number] {
  return { id: `Ticker:${ticker}`, label: 'Ticker', key: ticker, title: ticker, props: {}, tweet_url: null };
}

function alert(): Alert {
  return {
    id: 7,
    rule_name: 'unusual_options_sweep',
    event_id: 'evt_abc',
    tweet_id: 't7',
    tweet_url: 'https://x.com/y',
    title: 'NVDA sweep',
    summary: 'summary',
    graph_refs: ['NVDA'],
    created_at: '2026-09-30T00:00:00Z',
  };
}

const GRAPH: GraphData = {
  nodes: [eventNode('Event:evt_abc', 'evt_abc'), tickerNode('NVDA'), tickerNode('AMD')],
  links: [],
  node_count: 3,
  link_count: 0,
};

describe('resolveAlertNode', () => {
  it('prefers the exact event node', () => {
    expect(resolveAlertNode(alert(), GRAPH)?.id).toBe('Event:evt_abc');
  });

  it('falls back to the ticker when the event has not loaded yet', () => {
    const withoutEvent: GraphData = { ...GRAPH, nodes: GRAPH.nodes.filter((n) => n.label !== 'Event') };
    expect(resolveAlertNode(alert(), withoutEvent)?.id).toBe('Ticker:NVDA');
  });

  it('returns undefined when nothing matches or there is no graph', () => {
    expect(resolveAlertNode(alert(), null)).toBeUndefined();
    const unknown = { ...alert(), event_id: 'evt_nope', graph_refs: ['ZZZ'] };
    expect(resolveAlertNode(unknown, GRAPH)).toBeUndefined();
  });

  it('skips nullish graph refs instead of crashing', () => {
    const dirty = {
      ...alert(),
      event_id: 'evt_nope',
      graph_refs: [null, '', 42, 'AMD'] as unknown as string[],
    };
    expect(resolveAlertNode(dirty, GRAPH)?.id).toBe('Ticker:AMD');
    const allDirty = { ...dirty, graph_refs: [null] as unknown as string[] };
    expect(resolveAlertNode(allDirty, GRAPH)).toBeUndefined();
  });
});
