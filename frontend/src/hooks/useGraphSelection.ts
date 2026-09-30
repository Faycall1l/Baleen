import { useCallback, useRef } from 'react';

import type Graph from 'graphology';

import type { GraphNode } from '../types';

interface Args {
  graphRef: React.MutableRefObject<Graph | null>;
  onSelect: (node: GraphNode | null) => void;
}

/**
 * Selection entry point for the explorer.
 *
 * Clicking a node selects it for the inspector. Dropped (invisible) nodes
 * can never be selected because hit-testing only sees rendered nodes — and
 * the hasNode guard below makes that structural, not assumed.
 *
 * `onSelect` is read through a ref so the returned callback is stable:
 * sigma's click handlers rebind on every render otherwise.
 */
export function useGraphSelection({ graphRef, onSelect }: Args) {
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  return useCallback(
    (node: string) => {
      const g = graphRef.current;
      if (!g || !g.hasNode(node)) return;
      const a = g.getNodeAttributes(node);
      onSelectRef.current({
        id: node,
        label: a.kind,
        key: a.key,
        title: a.label,
        props: a.props,
        tweet_url: a.tweet_url ?? null,
      });
    },
    [graphRef],
  );
}
