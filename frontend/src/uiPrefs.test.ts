import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_FLOW,
  loadFlowFilters,
  loadHiddenTypes,
  saveFlowFilters,
  saveHiddenTypes,
} from './uiPrefs';

function installMemoryStorage(): void {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
}

describe('ui prefs', () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it('round-trips hidden types and drops garbage', () => {
    expect(loadHiddenTypes()).toEqual([]);
    saveHiddenTypes(new Set(['Event', 'Sector']));
    expect(loadHiddenTypes()).toEqual(['Event', 'Sector']);
  });

  it('round-trips flow filters with validated shapes', () => {
    expect(loadFlowFilters()).toEqual(DEFAULT_FLOW);
    saveFlowFilters({ tickers: ['NVDA'], side: 'call', min_ratio: 3, window_days: 7, limit: 5 });
    expect(loadFlowFilters()).toEqual({
      tickers: ['NVDA'],
      side: 'call',
      min_ratio: 3,
      window_days: 7,
      limit: 5,
    });
  });

  it('falls back to defaults on corrupt or mistyped stored filters', () => {
    localStorage.setItem(
      'baleen.ui.flowFilters',
      JSON.stringify({ tickers: 'NVDA', side: 'merge', min_ratio: 'high' }),
    );
    expect(loadFlowFilters()).toEqual(DEFAULT_FLOW);
  });
});
