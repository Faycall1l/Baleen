import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchActivity,
  fetchAlerts,
  fetchCommittee,
  fetchFlow,
  fetchGraph,
  fetchLeaderboard,
  fetchPath,
  fetchPerson,
  fetchReports,
  fetchRules,
  fetchSignals,
  fetchSimilar,
  fetchTicker,
  askQuestion,
} from './api';
import { DEFAULT_FLOW } from './uiPrefs';

function mockJson(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(body),
      }),
    ) as unknown as typeof fetch,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('card fetches', () => {
  it('normalizes null arrays on person cards', async () => {
    mockJson({ person: { id: 'P1', name: 'N', committees: null, trades: null } });
    const res = await fetchPerson('P1');
    expect(res.person.committees).toEqual([]);
    expect(res.person.trades).toEqual([]);
  });

  it('normalizes null arrays on ticker cards', async () => {
    mockJson({ ticker: { ticker: 'AMD', trades: null, flow: null } });
    const res = await fetchTicker('AMD');
    expect(res.ticker.trades).toEqual([]);
    expect(res.ticker.flow).toEqual([]);
  });

  it('normalizes null arrays on committee cards', async () => {
    mockJson({ committee: { id: 'C1', sectors: null, members: null, trades: null } });
    const res = await fetchCommittee('C1');
    expect(res.committee.sectors).toEqual([]);
    expect(res.committee.members).toEqual([]);
    expect(res.committee.trades).toEqual([]);
  });

  it('normalizes null arrays on query responses', async () => {
    mockJson({ question: 'q', columns: null, rows: null, followups: null, sources: null });
    const res = await askQuestion('q');
    expect(res.columns).toEqual([]);
    expect(res.rows).toEqual([]);
    expect(res.followups).toEqual([]);
    expect(res.sources).toEqual([]);
  });

  it('normalizes null lists on every feed payload', async () => {
    mockJson({ alerts: null, count: 0 });
    expect((await fetchAlerts()).alerts).toEqual([]);
    mockJson({ reports: null, count: 0 });
    expect((await fetchReports()).reports).toEqual([]);
    mockJson({ leaders: null, count: 0 });
    expect((await fetchLeaderboard()).leaders).toEqual([]);
    mockJson({ rules: null, count: 0 });
    expect((await fetchRules()).rules).toEqual([]);
    mockJson({ momentum: null, clusters: null, options_meter: null, uncovered: null, suspicious: null });
    const signals = await fetchSignals();
    expect(signals.momentum).toEqual([]);
    expect(signals.suspicious).toEqual([]);
    mockJson({ flow: null, count: 0, filters: DEFAULT_FLOW });
    expect((await fetchFlow(DEFAULT_FLOW)).flow).toEqual([]);
  });

  it('normalizes null nodes, links, and path segments', async () => {
    mockJson({ nodes: null, links: null, node_count: 0, link_count: 0 });
    const graph = await fetchGraph();
    expect(graph.nodes).toEqual([]);
    expect(graph.links).toEqual([]);
    mockJson({ from: 'a', to: 'b', nodes: null, rels: null, length: 0 });
    const path = await fetchPath('a', 'b');
    expect(path.nodes).toEqual([]);
    expect(path.rels).toEqual([]);
  });

  it('normalizes a null similar-traders list', async () => {
    mockJson({ similar: null });
    expect((await fetchSimilar('W000817')).similar).toEqual([]);
  });
});
