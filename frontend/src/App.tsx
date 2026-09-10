import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchAlerts, fetchDigest, fetchGraph, fetchHealth, fetchReports } from './api';
import AlertsPanel from './components/AlertsPanel';
import GraphView from './components/GraphView';
import NodeDetail from './components/NodeDetail';
import QueryBar from './components/QueryBar';
import ReportsPanel from './components/ReportsPanel';
import { theme } from './theme';
import type { Alert, GraphData, GraphNode, Health, QueryResponse, Report } from './types';

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

  const [refreshing, setRefreshing] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const graphSigRef = useRef<string | null>(null);

  const refresh = useCallback(() => {
    setRefreshing(true);
    Promise.all([loadGraph(), loadAlerts(), loadReports()]).finally(() => setRefreshing(false));
  }, [loadGraph, loadAlerts, loadReports]);

  useEffect(() => {
    loadGraph();
    loadAlerts();
    loadReports();
    // Poll so newly-ingested events appear live without a manual Refresh.
    const id = setInterval(() => {
      loadGraph();
      loadAlerts();
      loadReports();
    }, 30_000);
    return () => clearInterval(id);
  }, [loadGraph, loadAlerts, loadReports]);

  useEffect(() => {
    const checkHealth = () =>
      fetchHealth()
        .then(setHealth)
        .catch(() => setHealth(null));
    checkHealth();
    const id = setInterval(checkHealth, 15_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <h1 style={{ color: theme.accent }}>Baleen</h1>
          <span className="tagline">agentic graph over financial tweets</span>
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
        </aside>

        <main className="graph-area">
          <GraphView
            data={graph}
            error={graphError}
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
