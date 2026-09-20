import { useCallback, useEffect, useRef, useState } from 'react';

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

  const graphAbortRef = useRef<AbortController | null>(null);
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
    graphAbortRef.current?.abort();
    const ctrl = new AbortController();
    graphAbortRef.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    setGraphError(null);
    fetchGraph(150, ctrl.signal)
      .then((data) => {
        // Only swap the graph when something actually changed; keeping the
        // same data reference avoids needless re-renders on every poll.
        const sig = JSON.stringify({
          n: data.node_count,
          l: data.link_count,
          e: data.event_count,
          nodes: data.nodes.map((n) => n.id).sort(),
          links: data.links.map((x) => `${x.source}>${x.target}:${x.type}`).sort(),
        });
        if (sig !== graphSigRef.current) {
          graphSigRef.current = sig;
          setGraph(data);
        }
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setGraphError(err.message);
      })
      .finally(() => clearTimeout(timer));
  }, []);

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
              <AlertsPanel alerts={alerts} error={alertsError} />
            </div>
          )}
          {drawer === 'reports' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <ReportsPanel digest={digest} reports={reports} error={reportsError} />
            </div>
          )}
          {drawer === 'leaders' && (
            <div className="drawer">
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close panel">
                ×
              </button>
              <LeaderboardPanel leaders={leaders} error={leadersError} />
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
            <NodeDetail node={selected} onClose={() => setSelected(null)} />
          </aside>
        )}
      </div>
    </div>
  );
}
