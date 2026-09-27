import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { buildViewModel, eventTitle } from './view';

function node(
  id: string,
  label: string,
  title: string,
  props: Record<string, unknown> = {},
): GraphData['nodes'][number] {
  return { id, label, key: id, title, props, tweet_url: null };
}

const DATA: GraphData = {
  nodes: [
    node('Ticker:NVDA', 'Ticker', 'NVDA'),
    node('Politician:X', 'Politician', 'Xena X'),
    node('Event:e1', 'Event', 'x', {
      type: 'insider_trade',
      ticker: 'NVDA',
      transaction_type: 'buy',
      insider_name: 'Xena X',
    }),
  ],
  links: [
    { source: 'Event:e1', target: 'Ticker:NVDA', type: 'INVOLVES_TICKER' },
    { source: 'Event:e1', target: 'Politician:X', type: 'INVOLVES_POLITICIAN' },
  ],
  node_count: 3,
  link_count: 2,
};

const POS = new Map([
  ['Ticker:NVDA', { x: 0, y: 0 }],
  ['Politician:X', { x: 10, y: 0 }],
  ['Event:e1', { x: 5, y: 5 }],
  ['schema:Ticker', { x: 100, y: 100 }],
  ['schema:Politician', { x: 120, y: 100 }],
  ['schema:Event', { x: 110, y: 120 }],
]);

const BASE = { mode: 'instances' as const, hiddenTypes: new Set<string>(), selectedId: null as string | null, positions: POS };

describe('eventTitle', () => {
  it('composes informative titles', () => {
    expect(eventTitle({ type: 'insider_trade', ticker: 'NVDA', transaction_type: 'buy' })).toBe('NVDA bought');
    expect(eventTitle({ type: 'options_flow', ticker: 'NVDA', contract_type: 'call', strike: 250 })).toBe(
      'NVDA call 250',
    );
    expect(eventTitle({})).toBe('Unparsed');
  });
});

describe('buildViewModel instances', () => {
  it('renders visible nodes with names, sizes, and colors', () => {
    const vm = buildViewModel(DATA, BASE);
    expect(vm.nodes).toHaveLength(3);
    expect(vm.edges).toHaveLength(2);
    const tickers = vm.nodes.filter((n) => n.kind === 'Ticker');
    expect(tickers[0].label).toBe('NVDA');
    // Sizes follow the documented curves (events capped small).
    const sizes = new Map(vm.nodes.map((n) => [n.id, n.size]));
    expect(sizes.get('Ticker:NVDA')).toBeCloseTo(2.2 + 1.2, 9);
    expect(sizes.get('Event:e1')).toBeCloseTo(2.4 + Math.sqrt(2) * 1.0, 9);
    for (const n of vm.nodes) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(n.dimmed).toBe(false);
    }
  });

  it('hides filtered types structurally', () => {
    const vm = buildViewModel(DATA, { ...BASE, hiddenTypes: new Set(['Event']) });
    expect(vm.nodes.map((n) => n.id).sort()).toEqual(['Politician:X', 'Ticker:NVDA']);
    expect(vm.edges).toHaveLength(0);
  });

  it('dims outside the selection neighborhood', () => {
    const vm = buildViewModel(DATA, { ...BASE, selectedId: 'Ticker:NVDA' });
    const byId = new Map(vm.nodes.map((n) => [n.id, n]));
    expect(byId.get('Ticker:NVDA')!.dimmed).toBe(false);
    expect(byId.get('Event:e1')!.dimmed).toBe(false);
    expect(byId.get('Politician:X')!.dimmed).toBe(true);
    expect(byId.get('Ticker:NVDA')!.selected).toBe(true);
  });

  it('skips nodes without positions instead of rendering garbage', () => {
    const vm = buildViewModel(DATA, { ...BASE, positions: new Map() });
    expect(vm.nodes).toHaveLength(0);
    expect(vm.edges).toHaveLength(0);
  });
});

describe('buildViewModel schema', () => {
  it('aggregates types with counts and rels', () => {
    const vm = buildViewModel(DATA, { ...BASE, mode: 'schema' });
    const kinds = vm.nodes.map((n) => n.kind).sort();
    expect(kinds).toEqual(['Event', 'Politician', 'Ticker']);
    const tickers = vm.nodes.find((n) => n.kind === 'Ticker')!;
    expect(tickers.label).toContain('Ticker (1)');
    expect(vm.edges).toHaveLength(2);
    expect(vm.edges[0].label).toBe('involves ticker');
  });

  it('drops zero-count types', () => {
    const empty: GraphData = { nodes: [], links: [], node_count: 0, link_count: 0 };
    const vm = buildViewModel(empty, { ...BASE, mode: 'schema', positions: new Map() });
    expect(vm.nodes).toHaveLength(0);
  });
});
