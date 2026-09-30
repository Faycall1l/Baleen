import { useCallback, useMemo } from 'react';

import { resolveAlertNode } from '../alertNavigation';
import type { Alert, GraphData, GraphNode, LeaderboardEntry, Report } from '../types';
import type { Drawer } from '../components/PanelDrawers';

// Cross-links + inspector back-links (extracted from App, H5): resolve feed
// rows to graph nodes so every surface leads somewhere. Event nodes match by
// deterministic id; tickers and names match by key/title. All lookups are
// client-side over the loaded graph.
export function useInspectorLinks(
  graph: GraphData | null,
  alerts: Alert[],
  briefs: Report[],
  reports: Report[],
  selected: GraphNode | null,
  setDrawer: (d: Drawer) => void,
  handleSelect: (n: GraphNode | null) => void,
): {
  openAlertNode: (a: Alert) => boolean;
  openReportNode: (r: Report) => void;
  openLeaderNode: (l: LeaderboardEntry) => void;
  inspectorLinks: { label: string; onOpen: () => void }[];
} {
  const findEventNode = useCallback(
    (eventId: string): GraphNode | undefined => {
      if (!graph || !eventId) return undefined;
      return graph.nodes.find(
        (n) =>
          n.label === 'Event' &&
          ((n.props.id as string) === eventId || n.id === `Event:${eventId}`),
      );
    },
    [graph],
  );

  const findTickerNode = useCallback(
    (ticker: string): GraphNode | undefined => {
      if (!graph || !ticker) return undefined;
      const t = ticker.toUpperCase();
      return graph.nodes.find(
        (n) => n.label === 'Ticker' && (n.key === t || n.title === t),
      );
    },
    [graph],
  );

  const reveal = useCallback(
    (node: GraphNode | undefined): boolean => {
      if (!node) return false;
      setDrawer(null);
      handleSelect(node);
      return true;
    },
    [handleSelect, setDrawer],
  );

  const openAlertNode = useCallback(
    (a: Alert): boolean => {
      // The alert feed leads the graph snapshot by up to a poll cycle: when
      // the event (and every fallback) is missing, open the alerts drawer
      // so the click lands somewhere with context instead of dying.
      if (reveal(resolveAlertNode(a, graph))) return true;
      setDrawer('alerts');
      return false;
    },
    [graph, reveal, setDrawer],
  );

  const openReportNode = useCallback(
    (r: Report) => {
      if (r.event_id) reveal(findEventNode(r.event_id));
    },
    [findEventNode, reveal],
  );

  const openLeaderNode = useCallback(
    (l: LeaderboardEntry) => {
      reveal(findTickerNode(l.ticker));
    },
    [findTickerNode, reveal],
  );

  // What fired on / mentions the selected node.
  const inspectorLinks = useMemo(() => {
    if (!selected) return [];
    const links: { label: string; onOpen: () => void }[] = [];
    const eventId = selected.label === 'Event' ? (selected.props.id as string) : null;
    const names = new Set<string>([selected.title]);
    if (eventId) {
      const nAlerts = alerts.filter((a) => a.event_id === eventId).length;
      if (nAlerts > 0) {
        links.push({
          label: `${nAlerts} alert${nAlerts > 1 ? 's' : ''}`,
          onOpen: () => setDrawer('alerts'),
        });
      }
      const nBriefs = briefs.filter((r) => r.event_id === eventId).length;
      if (nBriefs > 0) {
        links.push({
          label: `${nBriefs} brief${nBriefs > 1 ? 's' : ''}`,
          onOpen: () => setDrawer('reports'),
        });
      }
    } else {
      const inRefs = (refs: string[] | undefined) =>
        (refs ?? []).some((r) => names.has(r));
      const nAlerts = alerts.filter((a) => inRefs(a.graph_refs)).length;
      if (nAlerts > 0) {
        links.push({
          label: `${nAlerts} alert${nAlerts > 1 ? 's' : ''}`,
          onOpen: () => setDrawer('alerts'),
        });
      }
      const nReports = reports.filter((r) => inRefs(r.graph_refs)).length;
      if (nReports > 0) {
        links.push({
          label: `${nReports} report${nReports > 1 ? 's' : ''}`,
          onOpen: () => setDrawer('reports'),
        });
      }
    }
    return links;
  }, [selected, alerts, briefs, reports, setDrawer]);

  return { openAlertNode, openReportNode, openLeaderNode, inspectorLinks };
}
