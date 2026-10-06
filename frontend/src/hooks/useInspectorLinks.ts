import { useCallback, useMemo } from 'react';

import { resolveAlertNode } from '../alertNavigation';
import { truncate } from '../graph/nodes';
import { alertsForNode, reportsForNode } from '../mentions';
import type { TileId } from '../tileState';
import type { Alert, GraphData, GraphNode, LeaderboardEntry, Report } from '../types';

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
  openPanel: (id: TileId | null) => void,
  handleSelect: (n: GraphNode | null) => void,
): {
  openAlertNode: (a: Alert) => boolean;
  openReportNode: (r: Report) => void;
  openLeaderNode: (l: LeaderboardEntry) => void;
  openTickerNode: (ticker: string) => boolean;
  openPoliticianNode: (politicianId: string) => boolean;
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
      openPanel(null);
      handleSelect(node);
      return true;
    },
    [handleSelect, openPanel],
  );

  const openAlertNode = useCallback(
    (a: Alert): boolean => {
      // The alert feed leads the graph snapshot by up to a poll cycle: when
      // the event (and every fallback) is missing, open the alerts tile
      // so the click lands somewhere with context instead of dying.
      if (reveal(resolveAlertNode(a, graph))) return true;
      openPanel('alerts');
      return false;
    },
    [graph, reveal, openPanel],
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

  const openTickerNode = useCallback(
    (ticker: string): boolean => {
      return reveal(findTickerNode(ticker));
    },
    [findTickerNode, reveal],
  );

  const openPoliticianNode = useCallback(
    (politicianId: string): boolean => {
      if (!graph || !politicianId) return false;
      const node = graph.nodes.find(
        (n) =>
          n.label === 'Politician' &&
          (n.key === politicianId || (n.props.id as string) === politicianId),
      );
      return reveal(node);
    },
    [graph, reveal],
  );

  // What fired on / mentions the selected node, as specific items — not
  // a count pointing at the general tile (R21). Each item opens its
  // exact subject; overflow falls back to the tile.
  const inspectorLinks = useMemo(() => {
    if (!selected) return [];
    const links: { label: string; onOpen: () => void }[] = [];
    const matchedAlerts = alertsForNode(selected, alerts);
    const matchedReports = reportsForNode(
      selected,
      selected.label === 'Event' ? briefs : reports,
      reports,
    );
    const MAX_ITEMS = 5;
    for (const a of matchedAlerts.slice(0, MAX_ITEMS)) {
      links.push({ label: `Alert: ${truncate(a.title, 42)}`, onOpen: () => void openAlertNode(a) });
    }
    for (const r of matchedReports.slice(0, MAX_ITEMS)) {
      links.push({ label: `Report: ${truncate(r.title, 42)}`, onOpen: () => void openReportNode(r) });
    }
    if (matchedAlerts.length > MAX_ITEMS || matchedReports.length > MAX_ITEMS) {
      links.push({
        label: `all in Alerts/Reports (${matchedAlerts.length + matchedReports.length})`,
        onOpen: () => openPanel(matchedAlerts.length >= matchedReports.length ? 'alerts' : 'reports'),
      });
    }
    return links;
  }, [selected, alerts, briefs, reports, openPanel, openAlertNode, openReportNode]);

  return { openAlertNode, openReportNode, openLeaderNode, openTickerNode, openPoliticianNode, inspectorLinks };
}
