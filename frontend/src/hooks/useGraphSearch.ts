import { useMemo, useState } from 'react';

import type { GraphData, GraphNode } from '../types';

interface Args {
  data: GraphData | null;
  mode: 'instances' | 'schema';
  hiddenTypes: Set<string>;
  setMode: (m: 'instances' | 'schema') => void;
  showType: (label: string) => void;
  onSelect: (node: GraphNode) => void;
}

export interface Search {
  query: string;
  setQuery: (q: string) => void;
  open: boolean;
  setOpen: (o: boolean) => void;
  matches: GraphNode[];
  choose: (m: GraphNode) => void;
}

/**
 * Toolbar search over node titles.
 *
 * Two characters minimum keeps the keystroke cost off a graph that can hold
 * tens of thousands of nodes, and the result list is capped at eight.
 * Choosing a hit also un-hides its type: selecting something the legend had
 * filtered out would otherwise select an invisible node and look broken.
 */
export function useGraphSearch({
  data,
  mode,
  hiddenTypes,
  setMode,
  showType,
  onSelect,
}: Args): Search {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!data || q.length < 2) return [];
    return data.nodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 8);
  }, [query, data]);

  const choose = (m: GraphNode) => {
    if (mode === 'schema') setMode('instances');
    const kind = data?.nodes.find((n) => n.id === m.id)?.label;
    if (kind && mode === 'instances' && hiddenTypes.has(kind)) showType(kind);
    onSelect(m);
    setQuery('');
    setOpen(false);
  };

  return { query, setQuery, open, setOpen, matches, choose };
}
