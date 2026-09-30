import { useCallback, useEffect, useRef, useState } from 'react';

import type { GraphData, GraphNode } from '../types';

// A freshly picked node outruns the graph snapshot by up to a poll cycle:
// the alert feed leads, the canvas follows. Give picks this long before the
// ghost release may clear them for vanishing from the loaded graph.
export const PICK_GRACE_MS = 60_000;

export function isGhostReleaseDue(pickedAtMs: number, nowMs: number, present: boolean): boolean {
  if (present) return false;
  return nowMs - pickedAtMs >= PICK_GRACE_MS;
}

// Selection ownership (extracted from App, H8): guarded select (a background
// click landing within a beat of picking is part of the same gesture) plus
// ghost release when polls drop the selected node.
export function useSelection(graph: GraphData | null): {
  selected: GraphNode | null;
  handleSelect: (n: GraphNode | null) => void;
  setSelected: React.Dispatch<React.SetStateAction<GraphNode | null>>;
} {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const lastPickRef = useRef(0);

  const handleSelect = useCallback((n: GraphNode | null) => {
    if (n) {
      lastPickRef.current = Date.now();
      setSelected(n);
    } else if (Date.now() - lastPickRef.current > 300) {
      setSelected(null);
    }
  }, []);

  useEffect(() => {
    if (!selected || !graph) return;
    const present = graph.nodes.some((n) => n.id === selected.id);
    if (isGhostReleaseDue(lastPickRef.current, Date.now(), present)) {
      setSelected(null);
    }
  }, [graph, selected]);

  return { selected, handleSelect, setSelected };
}
