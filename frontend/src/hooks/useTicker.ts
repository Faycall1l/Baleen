import { useEffect, useState } from 'react';

import { fetchTicker } from '../api';
import type { TickerCard } from '../types';

// Loads one ticker card for the inspector; refetches when the selected
// ticker changes. Stale responses lose the race via the cancelled flag.
export function useTicker(symbol: string | null) {
  const [card, setCard] = useState<TickerCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) {
      setCard(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchTicker(symbol)
      .then((res) => {
        if (!cancelled) setCard(res.ticker);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Ticker fetch failed');
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return { card, error };
}
