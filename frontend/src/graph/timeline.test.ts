import { describe, expect, it } from 'vitest';

import type { GraphData, GraphNode } from '../types';
import {
  buildTimelineRows,
  DAY_MS,
  dotRadius,
  maxVolume,
  timeDomain,
  viewWindow,
} from './timeline';

function event(
  id: string,
  props: Record<string, unknown>,
): GraphNode {
  return { id: `Event:${id}`, label: 'Event', key: id, title: id, props, tweet_url: null };
}

function data(nodes: GraphNode[]): GraphData {
  return {
    nodes,
    links: [],
    node_count: nodes.length,
    link_count: 0,
  };
}

const T0 = Date.parse('2026-09-01T00:00:00Z');

function flow(id: string, ticker: string, hoursAgo: number, extra: Record<string, unknown> = {}): GraphNode {
  return event(id, {
    type: 'options_flow',
    ticker,
    contract_type: 'call',
    volume: 1000,
    event_time: new Date(T0 - hoursAgo * 3_600_000).toISOString(),
    ...extra,
  });
}

describe('buildTimelineRows', () => {
  it('groups options-flow events by ticker, busiest row first', () => {
    const rows = buildTimelineRows(
      data([flow('a', 'AMD', 1), flow('b', 'NVDA', 2), flow('c', 'AMD', 3)]),
      new Set(),
    );
    expect(rows.map((r) => r.key)).toEqual(['flow:AMD', 'flow:NVDA']);
    expect(rows[0].section).toBe('flow');
    expect(rows[0].dots).toHaveLength(2);
    expect(rows[0].dots[0].time).toBeLessThan(rows[0].dots[1].time);
  });

  it('groups insider and congress trades into their own lanes', () => {
    const t = new Date(T0).toISOString();
    const insider = event('i1', { type: 'insider_trade', insider_name: 'Jane Doe', ticker: 'AMD', transaction_type: 'buy', event_time: t });
    const insider2 = event('i2', { type: 'insider_trade', insider_name: 'Jane Doe', ticker: 'NVDA', transaction_type: 'sell', event_time: t });
    const cong = event('c1', { type: 'congress_trade', politician_name: 'John Smith', ticker: 'AMD', transaction_type: 'buy', event_time: t });
    const rows = buildTimelineRows(data([insider, insider2, cong, flow('a', 'AMD', 1)]), new Set());
    expect(rows.map((r) => r.key)).toEqual([
      'flow:AMD',
      'insider:Jane Doe',
      'congress:John Smith',
    ]);
    const ins = rows.find((r) => r.section === 'insider')!;
    expect(ins.dots.map((d) => d.side)).toEqual(['buy', 'sell']);
    expect(ins.dots[0].r).toBe(4);
    // Hidden Insider/Politician types drop their lanes.
    const noIns = buildTimelineRows(data([insider]), new Set(['Insider']));
    expect(noIns).toEqual([]);
    const noPol = buildTimelineRows(data([cong]), new Set(['Politician']));
    expect(noPol).toEqual([]);
  });

  it('skips unknown types, bad timestamps, and honors hidden types', () => {
    const unknown = event('u1', { type: 'something_else', ticker: 'NVDA', event_time: new Date(T0).toISOString() });
    const timeless = flow('t', 'AMD', 1, { event_time: 'not-a-date' });
    const rows = buildTimelineRows(data([unknown, timeless, flow('a', 'AMD', 1)]), new Set());
    expect(rows).toHaveLength(1);
    expect(buildTimelineRows(data([flow('a', 'AMD', 1)]), new Set(['Event']))).toEqual([]);
    expect(buildTimelineRows(data([flow('a', 'AMD', 1)]), new Set(['Ticker']))).toEqual([]);
  });

  it('maps contract sides and caps rows', () => {
    const nodes = Array.from({ length: 20 }, (_, i) =>
      flow(`e${i}`, `T${i}`, 1, { contract_type: i % 2 === 0 ? 'put' : 'weird' }),
    );
    const rows = buildTimelineRows(data(nodes), new Set(), 5);
    expect(rows).toHaveLength(5);
    const sides = new Map(rows.map((r) => [r.key, r.dots[0].side]));
    expect(sides.get('flow:T0')).toBe('put');
    expect(sides.get('flow:T1')).toBe('other');
  });
});

describe('time helpers', () => {
  const rows = buildTimelineRows(
    data([flow('a', 'AMD', 48), flow('b', 'AMD', 1)]),
    new Set(),
  );

  it('computes the domain and zoom windows', () => {
    const domain = timeDomain(rows);
    expect(domain).not.toBeNull();
    expect(domain![1] - domain![0]).toBe(47 * 3_600_000);
    expect(viewWindow(domain, null)).toEqual(domain);
    const week = viewWindow(domain, 7)!;
    expect(week[1]).toBe(domain![1]);
    expect(week[0]).toBe(domain![1] - 7 * DAY_MS);
    expect(timeDomain([])).toBeNull();
    expect(viewWindow(null, 7)).toBeNull();
  });

  it('scales dot radii log-wise with a floor', () => {
    expect(maxVolume(rows)).toBe(1000);
    expect(dotRadius(0, 0)).toBe(2.5);
    expect(dotRadius(1000, 1000)).toBeCloseTo(7);
    const small = dotRadius(10, 1000);
    expect(small).toBeGreaterThan(2.5);
    expect(small).toBeLessThan(7);
  });
});
