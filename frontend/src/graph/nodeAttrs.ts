import type Graph from 'graphology';

import type { GraphData } from '../types';
import { eventTitle, nodeColor, nodeSize, shade, truncate } from './nodes';
import { SCHEMA_LABELS, SCHEMA_RADIUS } from './schema';

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

function schemaTitle(
  label: string,
  count: number,
  byId: Map<string, GraphData['nodes'][number]>,
  degree: Map<string, number>,
): string {
  if (count === 0) return `${label} (0)`;
  const tops = [...byId.values()]
    .filter((n) => n.label === label)
    .map((n) => ({
      t: n.label === 'Event' ? eventTitle(n.props) : n.title,
      d: degree.get(n.id) ?? 0,
    }))
    .sort((x, y) => y.d - x.d)
    .slice(0, 2)
    .map((t) => truncate(t.t, 15));
  return tops.length ? `${label} (${count}): ${tops.join(', ')}` : `${label} (${count})`;
}

/** Shared attrs for a supernode: survivor refresh and first add agree. */
export function schemaAttrs(
  label: string,
  count: number,
  byId: Map<string, GraphData['nodes'][number]>,
  degree: Map<string, number>,
): NodeAttrs {
  const color = nodeColor(label);
  return {
    type: 'bordered',
    kind: label,
    label: schemaTitle(label, count, byId, degree),
    key: label,
    title: schemaTitle(label, count, byId, degree),
    props: {},
    tweet_url: null,
    color,
    borderColor: shade(color, 0.55),
    size: 10 + Math.sqrt(count) * 1.5,
  };
}

/** Stable ring spot, so a supernode never lands on top of another one. */
export function schemaPosition(label: string): { x: number; y: number } {
  const idx = Math.max(SCHEMA_LABELS.indexOf(label), 0);
  const a = (idx / SCHEMA_LABELS.length) * 2 * Math.PI;
  return { x: Math.cos(a) * SCHEMA_RADIUS, y: Math.sin(a) * SCHEMA_RADIUS };
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
