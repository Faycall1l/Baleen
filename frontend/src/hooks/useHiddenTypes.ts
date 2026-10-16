import { useCallback, useState } from 'react';

import { SCHEMA_LABELS } from '../graph/schema';
import { soloHidden } from '../typeFilters';
import { loadHiddenTypes, saveHiddenTypes } from '../uiPrefs';

/** Hidden node types for the board legend. Lives above TimelineBoard so the
 * URL writer sees the whole view state; every change is persisted. */
export function useHiddenTypes(initial: string[]) {
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(
    () => new Set(initial.length > 0 ? initial : loadHiddenTypes()),
  );

  const toggleType = useCallback((label: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      saveHiddenTypes(next);
      return next;
    });
  }, []);

  const clearTypes = useCallback(() => {
    setHiddenTypes(() => {
      saveHiddenTypes([]);
      return new Set();
    });
  }, []);

  const soloType = useCallback((label: string) => {
    setHiddenTypes((prev) => {
      const next = soloHidden(SCHEMA_LABELS, label, prev);
      saveHiddenTypes(next);
      return next;
    });
  }, []);

  return { hiddenTypes, toggleType, clearTypes, soloType };
}
