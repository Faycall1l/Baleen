import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchAlerts, fetchDigest, fetchGraph, fetchHealth, fetchLeaderboard, fetchReports, fetchSignals } from './api';
import AlertsPanel from './components/AlertsPanel';
import GraphView from './components/GraphView';
import LeaderboardPanel from './components/LeaderboardPanel';
import NodeDetail from './components/NodeDetail';
import QueryBar from './components/QueryBar';
import ReportsPanel from './components/ReportsPanel';
import SignalsPanel from './components/SignalsPanel';
import { theme } from './theme';
import type { Alert, GraphData, GraphNode, Health, LeaderboardEntry, QueryResponse, Report, SignalsBundle } from './types';

function QueryResultView({ result }: { result: QueryResponse }) {
  return (
    <div className="query-result">
      <div className="answer">
        {result.has_data ? result.answer : 'No matching data in the graph yet.'}
      </div>
      <div className="meta">
        {result.allowed ? (
          <>
            <div>Generated Cypher:</div>
            <pre>{result.cypher}</pre>
            {result.sources.length > 0 && (
              <div>
                Sources:{' '}
                {result.sources.map((s, i) => (
                  <a key={i} href={s} target="_blank" rel="noreferrer">
                    [{i + 1}]
                  </a>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="error">
              Request blocked: {result.violations.join('; ')}
            </div>
            <pre>{result.cypher}</pre>
          </>
        )}
      </div>
    </div>
  );
}

function HealthPill({ health }: { health: Health | null }) {
  if (!health) {
    return (
      <span className="health-pill offline" title="Backend unreachable">
        offline
      </span>
    );
  }
  const down = Object.entries(health.checks)
    .filter(([, c]) => c.status !== 'ok')
    .map(([name]) => name);
  const title =
    down.length > 0
      ? `degraded: ${down.join(', ')} down`
      : 'all systems ok';
  return (
    <span
      className={`health-pill ${health.status === 'ok' ? 'ok' : 'degraded'}`}
      title={title}
    >
      {health.status}
    </span>
  );
}

export default function App() {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null);
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

  const loadGraph = useCallback(() => {
    setGraphError(null);
    fetchGraph()
      .then((data) => {
        // Only swap the graph when something actually changed; keeping the same
        // data reference stops react-force-graph from re-laying-out the view on
        // every poll.
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
      .catch((err) => setGraphError(err.message));
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

  const [refreshing, setRefreshing] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const graphSigRef = useRef<string | null>(null);

  const refresh = useCallback(() => {
    setRefreshing(true);
    Promise.all([loadGraph(), loadAlerts(), loadReports(), loadLeaders(), loadSignals()]).finally(() => setRefreshing(false));
  }, [loadGraph, loadAlerts, loadReports, loadLeaders, loadSignals]);

  useEffect(() => {
    loadGraph();
    loadAlerts();
    loadReports();
    loadLeaders();
    loadSignals();
    // Poll so newly-ingested events appear live without a manual Refresh.
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

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <img src="/baleen.svg" alt="Baleen logo" width={28} height={28} />
          <h1 style={{ color: theme.accent }}>Baleen</h1>
          <span className="tagline">watch Congress trade · ask anything</span>
        </div>
        <QueryBar onResult={setQueryResult} />
        <span className="graph-stats">
          {graph ? `${graph.node_count} nodes · ${graph.link_count} links` : ''}
          {graph?.truncated && graph.event_count
            ? ` · showing most recent of ${graph.event_count} events`
            : ''}
        </span>
        <HealthPill health={health} />
        <button onClick={refresh} disabled={refreshing}>
          {refreshing ? '…' : 'Refresh'}
        </button>
      </header>

      <div className="app-body">
        <aside className="panel panel-sidebar">
          {queryResult ? (
            <QueryResultView result={queryResult} />
          ) : (
            <div className="empty-hint">
              Ask a question about the graph in plain English — the agent turns
              it into a validated Cypher query and explains the answer.
            </div>
          )}
          <AlertsPanel alerts={alerts} error={alertsError} />
          <ReportsPanel digest={digest} reports={reports} error={reportsError} />
          <LeaderboardPanel leaders={leaders} error={leadersError} />
          <SignalsPanel signals={signals} error={signalsError} />
        </aside>

        <main className="graph-area">
          <GraphView
            data={graph}
            error={graphError}
            selectedId={selected?.id ?? null}
            onSelect={(n) => setSelected(n)}
          />
        </main>

        <aside className="panel panel-detail">
          {selected ? (
            <NodeDetail node={selected} onClose={() => setSelected(null)} />
          ) : (
            <div className="empty-hint">
              Select a node to inspect it and open its source tweet.
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
