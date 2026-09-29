import { useCallback, useEffect, useState } from 'react';

import { fetchFlow } from '../api';
import { FLOW_DEFAULTS } from '../flow';
import type { FlowFilters, FlowPrint } from '../types';

// Live options tape: poll on the same 30s cadence as the other feeds so the
// tape, graph, alerts, and reports stay in the same temporal frame.
export function useFlow() {
  const [filters, setFilters] = useState<FlowFilters>(FLOW_DEFAULTS);
  const [flow, setFlow] = useState<FlowPrint[]>([]);
  const [count, setCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

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
    const id = setInterval(load, 30_000);
    return () => {
      cancel();
      clearInterval(id);
    };
  }, [load]);

  return { flow, count, error, filters, setFilters, reload: load };
}
