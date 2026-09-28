import { useCallback, useRef } from 'react';

import type Graph from 'graphology';

import type { GraphNode } from '../types';
import { isSchemaId, SCHEMA_LABELS } from '../graph/schema';

interface Args {
  graphRef: React.MutableRefObject<Graph | null>;
  onSelect: (node: GraphNode | null) => void;
  onDrillToType: (hiddenTypes: Set<string>) => void;
}

/**
 * Selection entry point for the explorer.
 *
 * Schema nodes drill into their type; instance nodes select for the
 * inspector. Dropped (invisible) nodes can never be selected because
 * hit-testing only sees rendered nodes — and the hasNode guard below makes
 * that structural, not assumed.
 *
 * `onSelect` and the graph are read through refs so the returned callback is
 * stable: sigma's click handlers rebind on every render otherwise.
 */
export function useGraphSelection({ graphRef, onSelect, onDrillToType }: Args) {
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  return useCallback(
    (node: string) => {
      const g = graphRef.current;
      if (!g || !g.hasNode(node)) return;
      if (isSchemaId(node)) {
        // Drill-down keeps the type PLUS its directly related types (read off
        // the schema edges): isolating a single type would show dots with no
        // relationships, which is exactly as useless as it sounds.
        const label = node.slice('schema:'.length);
        const keep = new Set<string>([label]);
        g.forEachEdge((e) => {
          if (!e.startsWith('schema-rel:')) return;
          const [s, t] = g.extremities(e);
          const sl = g.getNodeAttribute(s, 'kind') as string;
          const tl = g.getNodeAttribute(t, 'kind') as string;
          if (sl === label && tl) keep.add(tl);
          if (tl === label && sl) keep.add(sl);
        });
        onDrillToType(new Set(SCHEMA_LABELS.filter((l) => !keep.has(l))));
        onSelectRef.current(null);
        return;
      }
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
    [graphRef, onDrillToType],
  );
}
