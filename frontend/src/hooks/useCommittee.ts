import { useEffect, useState } from 'react';

import { fetchCommittee } from '../api';
import type { CommitteeCard } from '../types';

// Loads one committee card for the inspector; refetches when the selected
// committee changes. Stale responses lose the race via the cancelled flag.
export function useCommittee(committeeId: string | null) {
  const [card, setCard] = useState<CommitteeCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!committeeId) {
      setCard(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchCommittee(committeeId)
      .then((res) => {
        if (!cancelled) setCard(res.committee);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Committee fetch failed');
      });
    return () => {
      cancelled = true;
    };
  }, [committeeId]);

  return { card, error };
}
