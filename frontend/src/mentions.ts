import type { Alert, GraphNode, Report } from './types';

// Which feed rows mention a node: an Event matches its own id;
// anything else matches rows whose graph_refs name it. Pure so the
// matching rules unit-test without a browser.
export function alertsForNode(node: GraphNode, alerts: Alert[]): Alert[] {
  if (node.label === 'Event') {
    const eventId = node.props.id as string | undefined;
    return alerts.filter((a) => a.event_id === eventId);
  }
  const names = new Set<string>([node.title]);
  return alerts.filter((a) => (a.graph_refs ?? []).some((r) => names.has(r)));
}

export function reportsForNode(node: GraphNode, briefs: Report[], reports: Report[]): Report[] {
  if (node.label === 'Event') {
    const eventId = node.props.id as string | undefined;
    return briefs.filter((r) => r.event_id === eventId);
  }
  const names = new Set<string>([node.title]);
  return reports.filter((r) => (r.graph_refs ?? []).some((x) => names.has(x)));
}
