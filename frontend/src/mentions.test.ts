import { describe, expect, it } from 'vitest';

import { alertsForNode, reportsForNode } from './mentions';
import type { Alert, GraphNode, Report } from './types';

function alert(id: number, event_id: string, graph_refs: string[]): Alert {
  return {
    id, rule_name: 'r', event_id, tweet_id: `t${id}`,
    tweet_url: 'https://x.com/y', title: `alert ${id}`, summary: 's',
    graph_refs, created_at: '2026-09-30T00:00:00Z',
  };
}

function report(id: number, event_id?: string, graph_refs: string[] = []): Report {
  return { id, kind: 'brief', event_id, title: `report ${id}`, body: 'b', graph_refs };
}

const EVENT: GraphNode = {
  id: 'Event:evt_1', label: 'Event', key: 'evt_1', title: 'NVDA call',
  props: { id: 'evt_1' }, tweet_url: null,
};

const TICKER: GraphNode = {
  id: 'Ticker:NVDA', label: 'Ticker', key: 'NVDA', title: 'NVDA',
  props: {}, tweet_url: null,
};

describe('mentions', () => {
  it('matches an event to its own alerts and briefs', () => {
    const alerts = [alert(1, 'evt_1', []), alert(2, 'evt_2', [])];
    expect(alertsForNode(EVENT, alerts).map((a) => a.id)).toEqual([1]);
    const briefs = [report(1, 'evt_1'), report(2, 'evt_2')];
    expect(reportsForNode(EVENT, briefs, briefs).map((r) => r.id)).toEqual([1]);
  });

  it('matches a ticker to rows naming it in graph_refs', () => {
    const alerts = [alert(1, 'evt_9', ['NVDA']), alert(2, 'evt_8', ['AMD'])];
    expect(alertsForNode(TICKER, alerts).map((a) => a.id)).toEqual([1]);
    const reports = [report(1, undefined, ['NVDA']), report(2, undefined, ['AMD'])];
    expect(reportsForNode(TICKER, [], reports).map((r) => r.id)).toEqual([1]);
  });
});
