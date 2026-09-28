import { useEffect, useState } from 'react';

import AlertStack from './components/AlertStack';
import CommandBar from './components/CommandBar';
import GraphErrorBoundary from './components/GraphErrorBoundary';
import GraphView from './components/GraphView';
import NodeDetail from './components/NodeDetail';
import PanelDrawers, { type Drawer } from './components/PanelDrawers';
import PanelRail from './components/PanelRail';
import { useFeeds } from './hooks/useFeeds';
import { useGraphFeed } from './hooks/useGraphFeed';
import { useInspectorLinks } from './hooks/useInspectorLinks';
import { useSelection } from './hooks/useSelection';

export default function App() {
  const { graph, graphError, graphStage, loadGraph } = useGraphFeed();
  const { selected, handleSelect, setSelected } = useSelection(graph);
  const [drawer, setDrawer] = useState<Drawer>(null);

  const {
    alerts,
    alertsError,
    reports,
    digest,
    reportsError,
    leaders,
    leadersError,
    signals,
    signalsError,
    health,
    briefs,
    reportCount,
    signalsCount,
    loadAll,
  } = useFeeds();

  useEffect(() => {
    loadAll();
    // Poll so newly-ingested events appear live.
    const id = setInterval(() => {
      loadGraph();
      loadAll();
    }, 30_000);
    return () => clearInterval(id);
  }, [loadGraph, loadAll]);

  const { openAlertNode, openReportNode, openLeaderNode, inspectorLinks } = useInspectorLinks(
    graph,
    alerts,
    briefs,
    reports,
    selected,
    setDrawer,
    handleSelect,
  );

  return (
    <div className="app">
      <CommandBar health={health} />

      <div className="app-body">
        <PanelRail
          drawer={drawer}
          onSelect={setDrawer}
          counts={{ alerts: alerts.length, reports: reportCount, leaders: leaders.length, signals: signalsCount }}
        />
        <main className="graph-area">
          <GraphErrorBoundary>
            <GraphView
              data={graph}
              error={graphError}
              selectedId={selected?.id ?? null}
              onSelect={handleSelect}
              loadingHint={
                graphStage === 'first' ? 'Loading graph (quick view)…' : undefined
              }
              warming={graphStage === 'partial'}
            />
          </GraphErrorBoundary>
          <PanelDrawers
            drawer={drawer}
            onClose={() => setDrawer(null)}
            alerts={alerts}
            alertsError={alertsError}
            onAlertClick={openAlertNode}
            digest={digest}
            reports={reports}
            reportsError={reportsError}
            onReportClick={openReportNode}
            leaders={leaders}
            leadersError={leadersError}
            onLeaderClick={openLeaderNode}
            signals={signals}
            signalsError={signalsError}
          />
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
        <AlertStack alerts={alerts} onAlertClick={openAlertNode} />
      </div>
    </div>
  );
}
