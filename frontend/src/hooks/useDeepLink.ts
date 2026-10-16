import { useEffect, useRef } from 'react';

import { fetchCommittee, fetchPerson, fetchTicker } from '../api';
import { buildGraphUrl } from '../graphUrl';
import { flattenLayout, type Layout } from '../tileState';
import type { GraphData, GraphNode } from '../types';

/** Select the deep-linked node once it loads. Consumed on first success, or on
 * any manual selection, so clearing it stays cleared. When the node never
 * loads (outside the window), entity cards resolve directly: Politician,
 * Ticker and Committee links open anywhere, so profiles are shareable even
 * with no graph context loaded. */
export function useDeepLinkNode(
  graph: GraphData | null,
  selected: GraphNode | null,
  node: string | null,
  handleSelect: (n: GraphNode | null) => void,
) {
  const consumed = useRef(false);
  useEffect(() => {
    if (consumed.current || !graph || !node) return;
    if (selected) {
      consumed.current = true;
      return;
    }
    const found = graph.nodes.find((n) => n.id === node);
    consumed.current = true;
    if (found) {
      handleSelect(found);
      return;
    }
    const sep = node.indexOf(':');
    const label = sep < 0 ? '' : node.slice(0, sep);
    const key = sep < 0 ? '' : node.slice(sep + 1);
    if (!key) return;
    let cancelled = false;
    const openCard = (title: string, props: Record<string, unknown>) => {
      if (!cancelled) handleSelect({ id: node, label, key, title, props });
    };
    if (label === 'Politician') {
      fetchPerson(key)
        .then((res) => openCard(res.person?.name ?? key, { id: key }))
        .catch(() => {});
    } else if (label === 'Ticker') {
      fetchTicker(key)
        .then((res) => openCard(res.ticker?.ticker ?? key, {}))
        .catch(() => {});
    } else if (label === 'Committee') {
      fetchCommittee(key)
        .then((res) => openCard(res.committee?.name ?? key, { id: key }))
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [graph, selected, node, handleSelect]);
}

/** Write the view state back to the URL on every change. */
export function useUrlWriteBack(selectedId: string | null, hiddenTypes: Set<string>, layout: Layout) {
  useEffect(() => {
    const url = buildGraphUrl({ node: selectedId, hidden: [...hiddenTypes], tiles: flattenLayout(layout) });
    window.history.replaceState(null, '', url);
  }, [selectedId, hiddenTypes, layout]);
}
