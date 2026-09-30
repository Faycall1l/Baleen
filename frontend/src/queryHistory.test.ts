import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HISTORY_CAP, loadHistory, saveQuestion } from './queryHistory';

function installMemoryStorage(): void {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
}

describe('query history', () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it('starts empty and ignores blanks', () => {
    expect(loadHistory()).toEqual([]);
    saveQuestion('   ');
    expect(loadHistory()).toEqual([]);
  });

  it('keeps most-recent-first, deduped, capped', () => {
    saveQuestion('a');
    saveQuestion('b');
    saveQuestion('a');
    expect(loadHistory()).toEqual(['a', 'b']);
    for (let i = 0; i < HISTORY_CAP + 5; i++) saveQuestion(`q${i}`);
    const history = loadHistory();
    expect(history).toHaveLength(HISTORY_CAP);
    expect(history[0]).toBe(`q${HISTORY_CAP + 4}`);
  });

  it('survives corrupt stored JSON', () => {
    localStorage.setItem('baleen.query.history', '{nope');
    expect(loadHistory()).toEqual([]);
  });
});
