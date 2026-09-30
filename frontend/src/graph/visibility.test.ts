import Graph from 'graphology';
import { describe, expect, it } from 'vitest';

import type { GraphData } from '../types';
import { applyVisibility } from './visibility';

function node(
  id: string,
  label: string,
  title: string,
): GraphData['nodes'][number] {
  return { id, label, key: id, title, props: {}, tweet_url: null };
}

const DATA: GraphData = {
  nodes: [
    node('Ticker:NVDA', 'Ticker', 'NVDA'),
    node('Politician:X', 'Politician', 'Xena X'),
    node('Event:e1', 'Event', 'x', ),
  ],
  links: [
    { source: 'Event:e1', target: 'Ticker:NVDA', type: 'INVOLVES_TICKER' },
    { source: 'Event:e1', target: 'Politician:X', type: 'INVOLVES_POLITICIAN' },
  ],
  node_count: 3,
  link_count: 2,
};

function freshGraph(): Graph {
  return new Graph({ type: 'directed', allowSelfLoops: true });
}

function assertFinite(graph: Graph): void {
  graph.forEachNode((id) => {
    const x = graph.getNodeAttribute(id, 'x');
    const y = graph.getNodeAttribute(id, 'y');
    expect(
      typeof x === 'number' && typeof y === 'number' && Number.isFinite(x) && Number.isFinite(y),
      `node ${id} placed`,
    ).toBe(true);
  });
}

describe('applyVisibility instances', () => {
  it('renders wanted nodes with positions and syncs edges', () => {
    const graph = freshGraph();
    const stats = applyVisibility(graph, DATA, new Set(), new Map());
    expect(stats.want).toBe(3);
    expect(stats.order).toBe(3);
    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);
    assertFinite(graph);
  });

  it('drops filtered types banking positions, restores on unhide', () => {
    const graph = freshGraph();
    const pos = new Map<string, { x: number; y: number }>();
    applyVisibility(graph, DATA, new Set(), pos);
    const before = {
      x: graph.getNodeAttribute('Ticker:NVDA', 'x'),
      y: graph.getNodeAttribute('Ticker:NVDA', 'y'),
    };
    applyVisibility(graph, DATA, new Set(['Ticker']), pos);
    expect(graph.hasNode('Ticker:NVDA')).toBe(false);
    expect(pos.get('Ticker:NVDA')).toEqual(before);
    applyVisibility(graph, DATA, new Set(), pos);
    expect(graph.hasNode('Ticker:NVDA')).toBe(true);
    expect(graph.getNodeAttribute('Ticker:NVDA', 'x')).toBe(before.x);
    expect(graph.getNodeAttribute('Ticker:NVDA', 'y')).toBe(before.y);
    assertFinite(graph);
  });

  it('drops payload removals', () => {
    const graph = freshGraph();
    const pos = new Map<string, { x: number; y: number }>();
    applyVisibility(graph, DATA, new Set(), pos);
    const smaller: GraphData = {
      ...DATA,
      nodes: DATA.nodes.slice(0, 1),
      links: [],
      node_count: 1,
      link_count: 0,
    };
    applyVisibility(graph, smaller, new Set(), pos);
    expect(graph.order).toBe(1);
    expect(graph.size).toBe(0);
  });
});

describe('applyVisibility guarantee', () => {
  it('repairs positionless nodes instead of leaving them', () => {
    const graph = freshGraph();
    graph.addNode('Ticker:NVDA', {});
    graph.addNode('junk', {});
    applyVisibility(graph, DATA, new Set(), new Map());
    assertFinite(graph);
    expect(graph.hasNode('junk')).toBe(false);
  });
});
