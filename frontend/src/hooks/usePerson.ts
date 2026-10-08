import { useEffect, useState } from 'react';

import { fetchPerson, fetchSimilar } from '../api';
import type { PersonCard, SimilarTrader } from '../types';

// Loads one politician card for the inspector; refetches when the selected
// person changes. Stale responses lose the race via the cancelled flag.
// Similar traders ride along so the card shows the co-trading network.
export function usePerson(politicianId: string | null) {
  const [person, setPerson] = useState<PersonCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [similar, setSimilar] = useState<SimilarTrader[]>([]);

  useEffect(() => {
    if (!politicianId) {
      setPerson(null);
      setError(null);
      setSimilar([]);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchPerson(politicianId)
      .then((res) => {
        if (!cancelled) setPerson(res.person);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Person fetch failed');
      });
    fetchSimilar(politicianId)
      .then((res) => {
        if (!cancelled) setSimilar(res.similar);
      })
      .catch(() => {
        if (!cancelled) setSimilar([]);
      });
    return () => {
      cancelled = true;
    };
  }, [politicianId]);

  return { person, error, similar };
}
