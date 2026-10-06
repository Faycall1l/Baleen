import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchCommittee, fetchPerson, fetchTicker } from './api';

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
});
