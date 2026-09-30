import type Graph from 'graphology';

import type { GraphData } from '../types';
import { eventTitle, nodeColor, nodeSize, shade } from './nodes';

const POS_CACHE_CAP = 2000;

export type NodeAttrs = Record<string, unknown>;

/** Bank a dropped node's position so re-adding it later doesn't reshuffle. */
export function bankPos(
  posCache: Map<string, { x: number; y: number }>,
  graph: Graph,
  id: string,
): void {
  const x = graph.getNodeAttribute(id, 'x') as number;
  const y = graph.getNodeAttribute(id, 'y') as number;
  if (Number.isFinite(x) && Number.isFinite(y)) {
    posCache.delete(id);
    posCache.set(id, { x, y });
    while (posCache.size > POS_CACHE_CAP) {
      const oldest = posCache.keys().next();
      if (oldest.done) break;
      posCache.delete(oldest.value);
    }
  }
}

/** Shared attrs for a payload node: survivor refresh and first add agree. */
export function instanceAttrs(
  n: GraphData['nodes'][number],
  degree: Map<string, number>,
): NodeAttrs {
  const color = nodeColor(n.label);
  const display = n.label === 'Event' ? eventTitle(n.props) : n.title;
  return {
    type: 'bordered',
    kind: n.label,
    label: display,
    key: n.key,
    title: display,
    props: n.props,
    tweet_url: n.tweet_url,
    color,
    borderColor: shade(color, 0.55),
    size: nodeSize(n.label, degree.get(n.id) ?? 0),
  };
}
