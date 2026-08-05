import { useCallback, useEffect, useState } from 'react';

import { fetchAlerts, fetchGraph } from './api';
import AlertsPanel from './components/AlertsPanel';
import GraphView from './components/GraphView';
import NodeDetail from './components/NodeDetail';
import QueryBar from './components/QueryBar';
import { theme } from './theme';
import type { Alert, GraphData, GraphNode, QueryResponse } from './types';

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

export default function App() {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  const loadGraph = useCallback(() => {
    setGraphError(null);
    fetchGraph()
      .then(setGraph)
      .catch((err) => setGraphError(err.message));
  }, []);

  const loadAlerts = useCallback(() => {
    setAlertsError(null);
    fetchAlerts()
      .then((res) => setAlerts(res.alerts))
      .catch((err) => setAlertsError(err.message));
  }, []);

  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(() => {
    setRefreshing(true);
    Promise.all([loadGraph(), loadAlerts()]).finally(() => setRefreshing(false));
  }, [loadGraph, loadAlerts]);

  useEffect(() => {
    loadGraph();
    loadAlerts();
    const id = setInterval(loadAlerts, 30_000);
    return () => clearInterval(id);
  }, [loadGraph, loadAlerts]);

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
        </span>
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
