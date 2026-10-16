import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchRules, setRuleActive, type AlertRule } from '../api';

// Alert rule mute state: loaded once on mount (rules change rarely),
// toggled optimistically with rollback on failure.
export function useAlertRules() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const rulesRef = useRef<AlertRule[]>([]);
  rulesRef.current = rules;

  useEffect(() => {
    let cancelled = false;
    fetchRules()
      .then((res) => {
        if (!cancelled) setRules(res.rules);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Rules fetch failed');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleRule = useCallback(async (name: string, active: boolean) => {
    const snapshot = rulesRef.current;
    setRules((current) => current.map((r) => (r.name === name ? { ...r, active } : r)));
    try {
      const updated = await setRuleActive(name, active);
      setRules((rows) => rows.map((r) => (r.name === updated.name ? updated : r)));
    } catch {
      setRules(snapshot);
    }
  }, []);

  return { rules, error, toggleRule };
}
