import { useEffect, useState } from 'react';

// Shared poll clock for the 30s refresh loop.
//
// The old design fired every feed at the same instant: graph + alerts +
// reports + leaders + signals + flow + health all hit the backend in one
// burst, and overlapping bursts piled Neo4j queries faster than they
// drained. Each caller takes its own phase offset so the
// bursts spread across the period, and ticks pause while the tab is hidden
// — a background tab has no business polling a loaded database.
//
// The clock starts at 1 (not 0) so effects depending on the tick still run
// their initial load on mount instead of waiting a full offset.
export function usePollingTick(periodMs: number, offsetMs: number): number {
  const [tick, setTick] = useState(1);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    const bump = () => {
      if (!document.hidden) setTick((t) => t + 1);
    };
    const start = setTimeout(() => {
      bump();
      interval = setInterval(bump, periodMs);
    }, offsetMs);
    const onVisible = () => {
      if (!document.hidden) setTick((t) => t + 1);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearTimeout(start);
      if (interval) clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [periodMs, offsetMs]);

  return tick;
}
