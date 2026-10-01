import { useCallback, useEffect, useState } from 'react';

import { fetchFlow } from '../api';
import type { FlowFilters, FlowPrint } from '../types';
import { loadFlowFilters, saveFlowFilters } from '../uiPrefs';
import { usePollingTick } from './usePollingTick';

// Live options tape: poll on the shared 30s cadence (flow phase :10/:40) so
// the tape, graph, alerts, and reports stay in the same temporal frame
// without bursting together. Filters persist across visits (R40).
export function useFlow() {
  const [filters, setFiltersState] = useState<FlowFilters>(() => loadFlowFilters());

  const setFilters = useCallback((next: FlowFilters | ((prev: FlowFilters) => FlowFilters)) => {
    setFiltersState((prev) => {
      const resolved = typeof next === 'function' ? next(prev) : next;
      saveFlowFilters(resolved);
      return resolved;
    });
  }, []);
  const [flow, setFlow] = useState<FlowPrint[]>([]);
  const [count, setCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const tick = usePollingTick(30_000, 10_000);

  const load = useCallback(() => {
    let cancelled = false;
    setError(null);
    fetchFlow(filters)
      .then((res) => {
        if (cancelled) return;
        setFlow(res.flow);
        setCount(res.count);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Flow feed failed');
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  useEffect(() => {
    const cancel = load();
    return cancel;
  }, [tick, load]);

  return { flow, count, error, filters, setFilters, reload: load };
}
