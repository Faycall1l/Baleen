import { useEffect, useState } from 'react';

import { fetchGraph } from '../api';
import { daysAgoToISO } from '../timeScrub';
import type { GraphData } from '../types';

/** Time scrubber: days-ago offset, null for live. The frozen snapshot replaces
 * the board data until cleared; live polls continue underneath so jumping back
 * is instant. */
export function useScrubber() {
  const [scrubDaysAgo, setScrubDaysAgo] = useState<number | null>(null);
  const [asOfData, setAsOfData] = useState<GraphData | null>(null);
  useEffect(() => {
    if (scrubDaysAgo === null) {
      setAsOfData(null);
      return;
    }
    let cancelled = false;
    fetchGraph(500, undefined, undefined, daysAgoToISO(scrubDaysAgo, Date.now()))
      .then((data) => {
        if (!cancelled) setAsOfData(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [scrubDaysAgo]);
  return { scrubDaysAgo, setScrubDaysAgo, asOfData };
}
