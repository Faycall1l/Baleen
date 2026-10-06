import type { Alert, GraphData, GraphNode } from './types';

// Resolves an alert row to the best graph node to reveal (R20).
//
// Order matters: the exact event first (full context), then the ticker,
// then a person name. Returns undefined when the event hasn't reached the
// loaded snapshot yet — callers must handle that (tile fallback) instead
// of silently dying, which is exactly what toasts used to do.
export function resolveAlertNode(alert: Alert, graph: GraphData | null): GraphNode | undefined {
  if (!graph) return undefined;
  const event = graph.nodes.find(
    (n) =>
      n.label === 'Event' &&
      ((n.props.id as string) === alert.event_id || n.id === `Event:${alert.event_id}`),
  );
  if (event) return event;
  for (const ref of alert.graph_refs) {
    const t = ref.toUpperCase();
    const ticker = graph.nodes.find(
      (n) => n.label === 'Ticker' && (n.key === t || n.title === t),
    );
    if (ticker) return ticker;
    const person = graph.nodes.find(
      (n) => (n.label === 'Politician' || n.label === 'Insider') && n.title === ref,
    );
    if (person) return person;
  }
  return undefined;
}
