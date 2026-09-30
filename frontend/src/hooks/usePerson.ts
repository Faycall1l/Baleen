import { useEffect, useState } from 'react';

import { fetchPerson } from '../api';
import type { PersonCard } from '../types';

// Loads one politician card for the inspector; refetches when the selected
// person changes. Stale responses lose the race via the cancelled flag.
export function usePerson(politicianId: string | null) {
  const [person, setPerson] = useState<PersonCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!politicianId) {
      setPerson(null);
      setError(null);
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
    return () => {
      cancelled = true;
    };
  }, [politicianId]);

  return { person, error };
}
