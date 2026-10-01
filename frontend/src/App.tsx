import { useEffect, useState } from 'react';

import AlertStack from './components/AlertStack';
import CommandBar from './components/CommandBar';
import FlowTape from './components/FlowTape';
import GraphErrorBoundary from './components/GraphErrorBoundary';
import GraphView from './components/GraphView';
import NodeDetail from './components/NodeDetail';
import OnboardingTour from './components/OnboardingTour';
import PanelDrawers, { type Drawer } from './components/PanelDrawers';
import PanelRail from './components/PanelRail';
import PoliticianDetail from './components/PoliticianDetail';
import ShortcutsHelp from './components/ShortcutsHelp';
import TickerDetail from './components/TickerDetail';
import { useFeeds } from './hooks/useFeeds';
import { useAlertsInbox } from './hooks/useAlertsInbox';
import { useFlow } from './hooks/useFlow';
import { useGraphFeed } from './hooks/useGraphFeed';
import { useInspectorLinks } from './hooks/useInspectorLinks';
import { usePerson } from './hooks/usePerson';
import { useTicker } from './hooks/useTicker';
import { usePollingTick } from './hooks/usePollingTick';
import { useSelection } from './hooks/useSelection';
import { loadOnboarded, saveOnboarded } from './onboarding';
import { personIdForNode } from './personView';

export default function App() {
  const { graph, graphError, graphStage, updatedAt, loadGraph } = useGraphFeed();
  const { selected, handleSelect, setSelected } = useSelection(graph);
  const { flow, count: flowCount, error: flowError, filters: flowFilters, setFilters: setFlowFilters, reload: reloadFlow } =
    useFlow();
  const [drawer, setDrawer] = useState<Drawer>(null);
  // Shortcut help (R39): `?` outside inputs toggles it.
  const [showHelp, setShowHelp] = useState(false);
  // Hovered relationship readout (R35) lives here so it can join the
  // status bar next to the counts.
  const [hoverEdge, setHoverEdge] = useState<string | null>(null);
  // First-run tour (R11): shown until dismissed, persisted across visits.
  const [showTour, setShowTour] = useState(() => !loadOnboarded());
  const dismissTour = () => {
    saveOnboarded();
    setShowTour(false);
  };

  // Staggered poll phases (R7): feeds at :00/:30, flow at :10/:40, graph at
  // :20/:50, so the heaviest request bursts never coincide on the database.
  // Ticks pause while the tab is hidden.
  const feedsTick = usePollingTick(30_000, 0);
  const graphTick = usePollingTick(30_000, 20_000);

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
  }, [feedsTick, loadAll]);

  useEffect(() => {
    loadGraph();
  }, [graphTick, loadGraph]);

  // Esc closes the open drawer from anywhere (R15); with no drawer it
  // clears the inspector selection (R16). `?` outside inputs opens help.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      if (e.key === '?' && !typing) {
        e.preventDefault();
        setShowHelp((v) => !v);
        return;
      }
      if (e.key === 'Escape') {
        if (showHelp) setShowHelp(false);
        else if (drawer) setDrawer(null);
        else setSelected(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer, showHelp]);

  // Alerts inbox (R8): unread watermark + rule/text filtering live here so
  // the rail badge and the drawer share one source of truth.
  const inbox = useAlertsInbox(alerts, drawer === 'alerts');

  const { openAlertNode, openReportNode, openLeaderNode, openTickerNode, inspectorLinks } = useInspectorLinks(
    graph,
    alerts,
    briefs,
    reports,
    selected,
    setDrawer,
    handleSelect,
  );

  // Person view (R17b): a selected Politician node loads its card instead
  // of the generic property list.
  const politicianId = selected
    ? personIdForNode(selected.label, selected.id, selected.props)
    : null;
  const { person, error: personError } = usePerson(politicianId);

  // Ticker view (R24b): same card pattern as politicians.
  const tickerSymbol =
    selected?.label === 'Ticker'
      ? (typeof selected.props.ticker === 'string' && selected.props.ticker) ||
        selected.id.split(':').slice(1).join(':') ||
        null
      : null;
  const { card: tickerCard, error: tickerError } = useTicker(tickerSymbol);

  // Cross-surface jump (R36): filter the tape to this ticker. The dock
  // shows it live on wide screens; on narrow screens the dock hides, so
  // open the flow drawer instead (same 1280px breakpoint as the CSS).
  const viewTickerInTape = (ticker: string) => {
    setFlowFilters((f) => ({ ...f, tickers: [ticker] }));
    if (window.matchMedia('(max-width: 1280px)').matches) setDrawer('flow');
  };

  return (
    <div className="app">
      <CommandBar health={health} />
      {showTour && <OnboardingTour onDone={dismissTour} />}
      {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}

      <div className="app-body">
        <PanelRail
          drawer={drawer}
          onSelect={setDrawer}
          counts={{ flow: flowCount, alerts: inbox.unread, reports: reportCount, leaders: leaders.length, signals: signalsCount }}
        />
        <aside className="tape-dock" aria-label="Options flow tape">
          <FlowTape
            flow={flow}
            count={flowCount}
            error={flowError}
            filters={flowFilters}
            onFiltersChange={setFlowFilters}
            onRetry={reloadFlow}
            onSelectTicker={openTickerNode}
          />
        </aside>
        <main className="graph-area">
          <GraphErrorBoundary>
            <GraphView
              data={graph}
              error={graphError}
              selectedId={selected?.id ?? null}
              onSelect={handleSelect}
              onHoverEdgeChange={setHoverEdge}
              onRetry={loadGraph}
              loadingHint={
                graphStage === 'first' ? 'Loading graph (quick view)…' : undefined
              }
              warming={graphStage === 'partial'}
            />
          </GraphErrorBoundary>
          <PanelDrawers
            drawer={drawer}
            onClose={() => setDrawer(null)}
            flow={flow}
            flowCount={flowCount}
            flowError={flowError}
            flowFilters={flowFilters}
            onFlowFiltersChange={setFlowFilters}
            onFlowRetry={reloadFlow}
            onFlowSelectTicker={openTickerNode}
            alerts={inbox.visible}
            alertsTotal={alerts.length}
            alertsRules={inbox.rules}
            alertsError={alertsError}
            onAlertClick={openAlertNode}
            alertRule={inbox.rule}
            onAlertRuleChange={inbox.setRule}
            alertQuery={inbox.query}
            onAlertQueryChange={inbox.setQuery}
            alertSeenMax={inbox.seenMax}
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
            {hoverEdge ? ` · rel: ${hoverEdge}` : ''}
            {updatedAt ? ` · sync ${new Date(updatedAt).toLocaleTimeString()}` : ''}
          </div>
        </main>

        {selected && (
          <aside className="panel panel-detail">
            {politicianId ? (
              <PoliticianDetail
                node={selected}
                person={person}
                personError={personError}
                alerts={alerts}
                onClose={() => setSelected(null)}
                onAlertClick={openAlertNode}
              />
            ) : tickerSymbol ? (
              <TickerDetail
                node={selected}
                card={tickerCard}
                cardError={tickerError}
                alerts={alerts}
                onClose={() => setSelected(null)}
                onAlertClick={openAlertNode}
                onViewInTape={viewTickerInTape}
              />
            ) : (
              <NodeDetail node={selected} onClose={() => setSelected(null)} links={inspectorLinks} />
            )}
          </aside>
        )}
        <AlertStack alerts={alerts} onAlertClick={openAlertNode} />
      </div>
    </div>
  );
}
