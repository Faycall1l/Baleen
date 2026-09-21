import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchAlerts, fetchDigest, fetchGraph, fetchHealth, fetchLeaderboard, fetchReports, fetchSignals } from './api';
import AlertsPanel from './components/AlertsPanel';
import AnswerPanel from './components/AnswerPanel';
import GraphView from './components/GraphView';
import LeaderboardPanel from './components/LeaderboardPanel';
import NodeDetail from './components/NodeDetail';
import QueryBar from './components/QueryBar';
import ReportsPanel from './components/ReportsPanel';
import SignalsPanel from './components/SignalsPanel';
import type { Alert, GraphData, GraphNode, Health, LeaderboardEntry, QueryResponse, Report, SignalsBundle } from './types';

function HealthDot({ health }: { health: Health | null }) {
  if (!health) {
    return <span className="health-dot offline" title="Backend unreachable" />;
  }
  const down = Object.entries(health.checks)
    .filter(([, c]) => c.status !== 'ok')
    .map(([name]) => name);
  const title = down.length > 0 ? `degraded: ${down.join(', ')} down` : 'all systems ok';
  const cls = health.status === 'ok' ? 'ok' : 'degraded';
  return <span className={`health-dot ${cls}`} title={title} />;
}

type Drawer = 'alerts' | 'reports' | 'leaders' | 'signals' | null;

export default function App() {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [answerOpen, setAnswerOpen] = useState(false);
  const [askSeed, setAskSeed] = useState<{ text: string; nonce: number } | null>(null);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [digest, setDigest] = useState<Report | null>(null);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);
  const [leadersError, setLeadersError] = useState<string | null>(null);
  const [signals, setSignals] = useState<SignalsBundle | null>(null);
  const [signalsError, setSignalsError] = useState<string | null>(null);

  // Refs mirror state so the stream can send fresh cursors on reconnect.
  const reportsRef = useRef(reports);
  reportsRef.current = reports;
  const alertsRef = useRef(alerts);
  alertsRef.current = alerts;
  const graphRef = useRef(graph);
  graphRef.current = graph;

  const graphAbortRef = useRef<AbortController | null>(null);
  // Poll counter: every 10th poll refetches full as the resync (deltas can
  // miss node deletions and tombstone merges, which only a full snapshot
  // heals). The backend applies the same idea via its cache bypass.
  const pollCountRef = useRef(0);

  // Newest event_time across the loaded snapshot: the delta cursor for the
  // next poll. Events carry ISO strings; unparseable values are ignored.
  const graphCursor = (g: GraphData | null): string | null => {
    if (!g) return null;
    let best: string | null = null;
    for (const n of g.nodes) {
      if (n.label !== 'Event') continue;
      const t = n.props.event_time;
      if (typeof t !== 'string' || t === '') continue;
      if (best === null || t > best) best = t;
    }
    return best;
  };

  const applyGraphPayload = useCallback((payload: GraphData, isDelta: boolean) => {
    if (isDelta) {
      if (payload.nodes.length === 0 && payload.links.length === 0) return;
      setGraph((prev) => {
        if (!prev) return payload;
        const nodes = new Map(prev.nodes.map((n) => [n.id, n]));
        for (const n of payload.nodes) nodes.set(n.id, n);
        const seen = new Set(prev.links.map((x) => `${x.source}>${x.target}:${x.type}`));
        const links = [...prev.links];
        for (const x of payload.links) {
          const k = `${x.source}>${x.target}:${x.type}`;
          if (!seen.has(k)) {
            seen.add(k);
            links.push(x);
          }
        }
        const merged: GraphData = {
          ...payload,
          nodes: [...nodes.values()],
          links,
          node_count: nodes.size,
          link_count: links.length,
          // Totals describe the snapshot, not the delta.
          event_count: prev.event_count,
          truncated: prev.truncated,
        };
        const sig = JSON.stringify({
          n: merged.node_count,
          l: merged.link_count,
          e: merged.event_count,
          nodes: merged.nodes.map((n) => n.id).sort(),
          links: merged.links.map((x) => `${x.source}>${x.target}:${x.type}`).sort(),
        });
        if (sig !== graphSigRef.current) {
          graphSigRef.current = sig;
          return merged;
        }
        return prev;
      });
      return;
    }
    const sig = JSON.stringify({
      n: payload.node_count,
      l: payload.link_count,
      e: payload.event_count,
      nodes: payload.nodes.map((n) => n.id).sort(),
      links: payload.links.map((x) => `${x.source}>${x.target}:${x.type}`).sort(),
    });
    if (sig !== graphSigRef.current) {
      graphSigRef.current = sig;
      setGraph(payload);
    }
  }, []);
  // Guards the selection-clear race: a background click landing within a
  // beat of picking a node is treated as part of the same gesture.
  const lastPickRef = useRef(0);

  const handleSelect = useCallback((n: GraphNode | null) => {
    if (n) {
      lastPickRef.current = Date.now();
      setSelected(n);
    } else if (Date.now() - lastPickRef.current > 300) {
      setSelected(null);
    }
  }, []);

  const loadGraph = useCallback(() => {
    // Slow renders must never pile up: a new poll cancels the previous
    // in-flight fetch (aborts are swallowed, not surfaced as errors).
    // Steady-state polls ask for a delta off the newest event_time (cheap
    // index-bounded query); every 10th poll refetches full as the resync.
    graphAbortRef.current?.abort();
    const ctrl = new AbortController();
    graphAbortRef.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    setGraphError(null);
    pollCountRef.current += 1;
    const cursor = graphCursor(graphRef.current);
    const full = pollCountRef.current % 10 === 1 || cursor === null;
    fetchGraph(150, ctrl.signal, full ? undefined : cursor ?? undefined)
      .then((data) => applyGraphPayload(data, !full))
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setGraphError(err.message);
      })
      .finally(() => clearTimeout(timer));
  }, [applyGraphPayload]);

  const loadAlerts = useCallback(() => {
    setAlertsError(null);
    fetchAlerts()
      .then((res) => setAlerts(res.alerts))
      .catch((err) => setAlertsError(err.message));
  }, []);

  const loadReports = useCallback(() => {
    setReportsError(null);
    fetchReports()
      .then((res) => setReports(res.reports))
      .catch((err) => setReportsError(err.message));
    // The digest generates on first request; failure must not blank briefs.
    fetchDigest()
      .then((res) => setDigest(res.report))
      .catch(() => {});
  }, []);

  const loadLeaders = useCallback(() => {
    setLeadersError(null);
    fetchLeaderboard()
      .then((res) => setLeaders(res.leaders))
      .catch((err) => setLeadersError(err.message));
  }, []);

  const loadSignals = useCallback(() => {
    setSignalsError(null);
    fetchSignals()
      .then(setSignals)
      .catch((err) => setSignalsError(err.message));
  }, []);

  const [health, setHealth] = useState<Health | null>(null);
  const graphSigRef = useRef<string | null>(null);

  useEffect(() => {
    loadGraph();
    loadAlerts();
    loadReports();
    loadLeaders();
    loadSignals();
    // Poll so newly-ingested events appear live.
    const id = setInterval(() => {
      loadGraph();
      loadAlerts();
      loadReports();
      loadLeaders();
      loadSignals();
    }, 30_000);
    return () => clearInterval(id);
  }, [loadGraph, loadAlerts, loadReports, loadLeaders, loadSignals]);

  useEffect(() => {
    const checkHealth = () => fetchHealth().then(setHealth);
    checkHealth();
    const id = setInterval(checkHealth, 15_000);
    return () => clearInterval(id);
  }, []);

  // Live stream: append newly published reports/alerts as they land. The
  // 30s poll above stays as the history loader and fallback; appends dedupe
  // by id so races between the two are harmless. Manual reconnect (with
  // fresh cursors) instead of EventSource's built-in retry, which would
  // replay from stale cursors.
  useEffect(() => {
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const appendUnique = <T extends { id: number }>(
      setList: React.Dispatch<React.SetStateAction<T[]>>,
      row: T,
    ) => {
      setList((prev) => (prev.some((r) => r.id === row.id) ? prev : [...prev, row]));
    };

    const connect = () => {
      if (closed) return;
      const mr = reportsRef.current.reduce((m, r) => Math.max(m, r.id), 0);
      const ma = alertsRef.current.reduce((m, a) => Math.max(m, a.id), 0);
      es = new EventSource(`/api/stream?after_report=${mr}&after_alert=${ma}`);
      es.addEventListener('report', (ev) => {
        try {
          appendUnique(setReports, JSON.parse((ev as MessageEvent).data) as Report);
        } catch {
          /* ignore malformed chunk */
        }
      });
      es.addEventListener('alert', (ev) => {
        try {
          appendUnique(setAlerts, JSON.parse((ev as MessageEvent).data) as Alert);
        } catch {
          /* ignore malformed chunk */
        }
      });
      es.onerror = () => {
        es?.close();
        es = null;
        if (!closed) timer = setTimeout(connect, 5000);
      };
    };
    connect();
    return () => {
      closed = true;
      es?.close();
      if (timer) clearTimeout(timer);
    };
  }, []);

  const briefs = reports.filter((r) => r.kind === 'brief');
  const reportCount = briefs.length + (digest ? 1 : 0);
  const signalsCount = signals
    ? signals.momentum.length +
      signals.clusters.length +
      signals.options_meter.length +
      signals.suspicious.length +
      signals.uncovered.length
    : 0;

  // Cross-links: resolve feed rows to graph nodes so every surface leads
  // somewhere. Event nodes match by deterministic id; tickers and names
  // match by key/title. All lookups are client-side over the loaded graph.
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

  const findNameNode = useCallback(
    (name: string): GraphNode | undefined => {
      if (!graph || !name) return undefined;
      return graph.nodes.find(
        (n) =>
          (n.label === 'Politician' || n.label === 'Insider') && n.title === name,
      );
    },
    [graph],
  );

  const reveal = useCallback(
    (node: GraphNode | undefined) => {
      if (!node) return;
      setDrawer(null);
      handleSelect(node);
    },
    [handleSelect],
  );

  const openAlertNode = useCallback(
    (a: Alert) => {
      const node =
        findEventNode(a.event_id) ??
        a.graph_refs.map((r) => findTickerNode(r) ?? findNameNode(r)).find(Boolean);
      reveal(node);
    },
    [findEventNode, findTickerNode, findNameNode, reveal],
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

  // Inspector back-links: what fired on / mentions the selected node.
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
  }, [selected, alerts, briefs, reports]);

  const rail = (
    <nav className="rail" aria-label="Panels">
      {(
        [
          { key: 'alerts', label: 'Alerts', count: alerts.length },
          { key: 'reports', label: 'Reports', count: reportCount },
          { key: 'leaders', label: 'Board', count: leaders.length },
          { key: 'signals', label: 'Signals', count: signalsCount },
        ] as { key: Exclude<Drawer, null>; label: string; count: number }[]
      ).map(({ key, label, count }) => (
        <button
          key={key}
          className={`rail-btn${drawer === key ? ' active' : ''}`}
          onClick={() => setDrawer(drawer === key ? null : key)}
          title={label}
        >
          <span className="rail-label">{label}</span>
          {count > 0 && <span className="rail-badge">{count}</span>}
        </button>
      ))}
    </nav>
  );

  return (
    <div className="app">
      <header className="commandbar">
        <div className="brand">
          <img src="/baleen.svg" alt="Baleen logo" width={26} height={26} />
          <h1>Baleen</h1>
        </div>
        <div className="ask-wrap">
          <QueryBar
            onResult={(res) => {
              setQueryResult(res);
              setAnswerOpen(true);
            }}
            onError={setQueryError}
            onFocusAsk={() => setAnswerOpen(true)}
            externalAsk={askSeed}
          />
          {answerOpen && (
            <AnswerPanel
              result={queryResult}
              error={queryError}
              onClose={() => setAnswerOpen(false)}
              onExample={(q) => setAskSeed({ text: q, nonce: Date.now() })}
            />
          )}
        </div>
        <HealthDot health={health} />
      </header>

      <div className="app-body">
        {rail}
        <main className="graph-area">
          <GraphView
            data={graph}
            error={graphError}
            selectedId={selected?.id ?? null}
            onSelect={handleSelect}
          />
          {drawer === 'alerts' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <AlertsPanel alerts={alerts} error={alertsError} onAlertClick={openAlertNode} />
            </div>
          )}
          {drawer === 'reports' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <ReportsPanel digest={digest} reports={reports} error={reportsError} onReportClick={openReportNode} />
            </div>
          )}
          {drawer === 'leaders' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <LeaderboardPanel leaders={leaders} error={leadersError} onLeaderClick={openLeaderNode} />
            </div>
          )}
          {drawer === 'signals' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <SignalsPanel signals={signals} error={signalsError} />
            </div>
          )}
          <div className="graph-status">
            {graph ? `${graph.node_count} nodes · ${graph.link_count} links` : ''}
            {graph?.truncated && graph.event_count
              ? ` · recent ${graph.node_count} of ${graph.event_count} events`
              : ''}
          </div>
        </main>

        {selected && (
          <aside className="panel panel-detail">
            <NodeDetail node={selected} onClose={() => setSelected(null)} links={inspectorLinks} />
          </aside>
        )}
      </div>
    </div>
  );
}
