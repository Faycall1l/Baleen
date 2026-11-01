import { useCallback, useEffect, useMemo, useState } from 'react';

import AlertStack from './components/AlertStack';
import AlertsPanel from './components/AlertsPanel';
import LeaderboardPanel from './components/LeaderboardPanel';
import LeadsPanel from './components/LeadsPanel';
import ReportsPanel from './components/ReportsPanel';
import InsightsPanel from './components/InsightsPanel';
import ActivityPanel from './components/ActivityPanel';
import MediaPanel from './components/MediaPanel';
import CommandBar from './components/CommandBar';
import FlowTape from './components/FlowTape';
import GraphErrorBoundary from './components/GraphErrorBoundary';
import TimelineBoard from './components/TimelineBoard';
import NodeDetail from './components/NodeDetail';
import OnboardingTour from './components/OnboardingTour';
import PanelRail, { type RailKey } from './components/PanelRail';
import PathPanel from './components/PathPanel';
import TileDock from './components/TileDock';
import PathFromButton from './components/PathFromButton';
import CommitteeDetail from './components/CommitteeDetail';
import PoliticianDetail from './components/PoliticianDetail';
import ShortcutsHelp from './components/ShortcutsHelp';
import TickerDetail from './components/TickerDetail';
import { useFeeds } from './hooks/useFeeds';
import { useAlertsInbox } from './hooks/useAlertsInbox';
import { useAlertRules } from './hooks/useAlertRules';
import { useFlow } from './hooks/useFlow';
import { useDeepLinkNode, useUrlWriteBack } from './hooks/useDeepLink';
import { useGraphFeed } from './hooks/useGraphFeed';
import { useHiddenTypes } from './hooks/useHiddenTypes';
import { usePathMode } from './hooks/usePathMode';
import { useScrubber } from './hooks/useScrubber';
import { useInspectorLinks } from './hooks/useInspectorLinks';
import { useCommittee } from './hooks/useCommittee';
import { usePerson } from './hooks/usePerson';
import { useTicker } from './hooks/useTicker';
import { usePollingTick } from './hooks/usePollingTick';
import { useSelection } from './hooks/useSelection';
import { loadOnboarded, saveOnboarded } from './onboarding';
import { fetchPerson, fetchTicker } from './api';
import { personIdForNode } from './personView';
import { parseGraphUrl } from './graphUrl';
import { loadLayout, saveLayout } from './uiPrefs';
import {
  closeInLayout,
  flattenLayout,
  moveSibling,
  openInLayout,
  sanitizeLayout,
  splitTile,
  toggleInLayout,
  type Layout,
  type TileId,
} from './tileState';
import { SCRUB_DAYS } from './timeScrub';

export default function App() {
  const { graph, graphError, graphStage, updatedAt, loadGraph, showEvents, setShowEvents } = useGraphFeed();
  const { selected, handleSelect, setSelected } = useSelection(graph);
  const { flow, count: flowCount, error: flowError, filters: flowFilters, setFilters: setFlowFilters, reload: reloadFlow } =
    useFlow();
  // Deep-link state: selection, hidden types, and open
  // panels encoded in the URL so any view is shareable. Read once on mount;
  // written back on every change below. An explicit tiles param beats prefs;
  // otherwise the workspace restores the last layout.
  const [initialUrl] = useState(() => parseGraphUrl(window.location.search));
  // Tile dock: rail-opened panels share the main area. Persisted like
  // other workspace prefs; the board is permanent (see tileState).
  const [layout, setLayout] = useState<Layout>(() =>
    initialUrl.tiles ? sanitizeLayout(initialUrl.tiles) : loadLayout(),
  );
  const [enlarged, setEnlarged] = useState<TileId | null>(null);
  useEffect(() => {
    saveLayout(layout);
  }, [layout]);
  const { hiddenTypes, toggleType, clearTypes, soloType } = useHiddenTypes(initialUrl.hidden);
  // Shortcut help: `?` outside inputs toggles it.
  const [showHelp, setShowHelp] = useState(false);
  // First-run tour: shown until dismissed, persisted across visits.
  const [showTour, setShowTour] = useState(() => !loadOnboarded());
  const dismissTour = () => {
    saveOnboarded();
    setShowTour(false);
  };

  // Staggered poll phases: feeds at :00/:30, flow at :10/:40, graph at
  // :20/:50, so the heaviest request bursts never coincide on the database.
  // Ticks pause while the tab is hidden.
  const feedsTick = usePollingTick(30_000, 0);
  const graphTick = usePollingTick(30_000, 20_000);

  const { scrubDaysAgo, setScrubDaysAgo, asOfData } = useScrubber();

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
    activity,
    activityError,
    news,
    newsError,
    health,
    healthChecked,
    briefs,
    reportCount,
    activityCount,
    newsCount,
    loadAll,
  } = useFeeds();

  useEffect(() => {
    loadAll();
  }, [feedsTick, loadAll]);

  useEffect(() => {
    loadGraph();
  }, [graphTick, loadGraph]);

  useDeepLinkNode(graph, selected, initialUrl.node, handleSelect);
  useUrlWriteBack(selected?.id ?? null, hiddenTypes, layout);

  const path = usePathMode(handleSelect, () => setLayout((prev) => openInLayout(prev, 'path')));
  const { pathFrom, setPathFrom, pathResult, pathError } = path;

  // Esc restores an enlarged tile, disarms path mode, or clears the
  // inspector selection. `?` outside inputs opens help.
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
        else if (enlarged) setEnlarged(null);
        else if (pathFrom) setPathFrom(null);
        else setSelected(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enlarged, showHelp, pathFrom, setPathFrom, setSelected]);

  // Alerts inbox: unread watermark + rule/text filtering live here so
  // the rail badge and the tile share one source of truth.
  const openTiles = useMemo(() => flattenLayout(layout), [layout]);
  const inbox = useAlertsInbox(alerts, openTiles.includes('alerts'));
  const { rules: alertRules, toggleRule: toggleAlertRule } = useAlertRules();

  const loadedIds = useMemo(
    () => new Set((graph?.nodes ?? []).map((n) => n.id)),
    [graph],
  );

  const selectPathNode = (id: string) => {
    const full = graph?.nodes.find((n) => n.id === id);
    if (full) handleSelect(full);
  };

  const handleBoardSelect = path.completeOrSelect;

  // Member clicks from any surface: the graph node when loaded, else the
  // person card as a synthetic node. Never a dead click.
  const openMemberAnywhere = (pid: string) => {
    if (openPoliticianNode(pid)) return;
    fetchPerson(pid)
      .then((res) => {
        if (res.person) {
          handleSelect({
            id: `Politician:${pid}`,
            label: 'Politician',
            key: pid,
            title: res.person.name ?? pid,
            props: { id: pid },
          });
        }
      })
      .catch(() => {});
  };
  // Cross-surface panel opener for the inspector links: null restores an
  // enlarged dock so the revealed node is visible; an id opens its tile.
  const openTile = useCallback((id: TileId | null) => {
    if (id === null) {
      setEnlarged(null);
      return;
    }
    setLayout((prev) => openInLayout(prev, id));
  }, []);

  const { openAlertNode, openReportNode, openLeaderNode, openTickerNode, openPoliticianNode, inspectorLinks } = useInspectorLinks(
    graph,
    alerts,
    briefs,
    reports,
    selected,
    openTile,
    handleSelect,
  );

  // Person view: a selected Politician node loads its card instead
  // of the generic property list.
  const politicianId = selected
    ? personIdForNode(selected.label, selected.id, selected.props)
    : null;
  const { person, error: personError, similar } = usePerson(politicianId);

  // Ticker view: same card pattern as politicians.
  const tickerSymbol =
    selected?.label === 'Ticker'
      ? (typeof selected.props.ticker === 'string' && selected.props.ticker) ||
        selected.id.split(':').slice(1).join(':') ||
        null
      : null;
  const { card: tickerCard, error: tickerError } = useTicker(tickerSymbol);

  // Committee view: same card pattern as politicians and tickers.
  const committeeId =
    selected?.label === 'Committee'
      ? (typeof selected.props.id === 'string' && selected.props.id) ||
        selected.id.split(':').slice(1).join(':') ||
        null
      : null;
  const { card: committeeCard, error: committeeError } = useCommittee(committeeId);

  // Cross-surface jump: filter the tape to this ticker and make
  // sure its tile is open.
  const viewTickerInTape = (ticker: string) => {
    setFlowFilters((f) => ({ ...f, tickers: [ticker] }));
    setLayout((prev) => openInLayout(prev, 'flow'));
  };

  // Rail toggle: every tab docks its tile, board included.
  const handleRailToggle = (key: RailKey) => {
    setLayout((prev) => toggleInLayout(prev, key));
  };
  const railOpen: RailKey[] = [...openTiles];

  // Ticker clicks from any surface: the graph node when loaded, else the
  // ticker card as a synthetic node (deep-link mechanism), else
  // the filtered tape. Never opens an empty tape blind.
  const openSignalTicker = (t: string) => {
    if (openTickerNode(t)) return;
    fetchTicker(t)
      .then((res) => {
        if (res.ticker) {
          handleSelect({
            id: `Ticker:${t}`,
            label: 'Ticker',
            key: t,
            title: res.ticker.ticker ?? t,
            props: {},
          });
        } else {
          viewTickerInTape(t);
        }
      })
      .catch(() => viewTickerInTape(t));
  };

  const renderTileBody = (id: TileId) => {
    if (id === 'insights') {
      return (
        <div className="flow-tile-scroll">
          <InsightsPanel signals={signals} leaders={leaders} activity={activity} unreadAlerts={inbox.unread} onTickerClick={openSignalTicker} />
        </div>
      );
    }
    if (id === 'leads') {
      return (
        <div className="flow-tile-scroll">
          <LeadsPanel signals={signals} error={signalsError} onTickerClick={openSignalTicker} />
        </div>
      );
    }
    if (id === 'board') {
      return (
        <GraphErrorBoundary>
          <TimelineBoard
            data={asOfData ?? graph}
            error={graphError}
            selectedId={selected?.id ?? null}
            onSelect={handleBoardSelect}
            onRetry={loadGraph}
            hiddenTypes={hiddenTypes}
            onToggleType={toggleType}
            onSoloType={soloType}
            onClearTypes={clearTypes}
            scrubDaysAgo={scrubDaysAgo}
            scrubMaxDays={SCRUB_DAYS}
            onScrubChange={setScrubDaysAgo}
            showEvents={showEvents}
            onToggleEvents={() => setShowEvents((v) => !v)}
            loadingHint={
              graphStage === 'first' ? 'Loading board (quick view)…' : undefined
            }
            warming={graphStage === 'partial'}
          />
        </GraphErrorBoundary>
      );
    }
    if (id === 'flow') {
      return (
        <div className="flow-tile-scroll">
          <FlowTape
            flow={flow}
            count={flowCount}
            error={flowError}
            filters={flowFilters}
            onFiltersChange={setFlowFilters}
            onRetry={reloadFlow}
            onSelectTicker={openTickerNode}
          />
        </div>
      );
    }
    if (id === 'alerts') {
      return (
        <div className="flow-tile-scroll">
          <AlertsPanel
            alerts={inbox.visible}
            total={alerts.length}
            rules={inbox.rules}
            ruleStates={alertRules}
            error={alertsError}
            onAlertClick={openAlertNode}
            rule={inbox.rule}
            onRuleChange={inbox.setRule}
            query={inbox.query}
            onQueryChange={inbox.setQuery}
            onToggleRule={toggleAlertRule}
            seenMax={inbox.seenMax}
          />
        </div>
      );
    }
    if (id === 'reports') {
      return (
        <div className="flow-tile-scroll">
          <ReportsPanel
            digest={digest}
            reports={reports}
            error={reportsError}
            onReportClick={openReportNode}
          />
        </div>
      );
    }
    if (id === 'leaders') {
      return (
        <div className="flow-tile-scroll">
          <LeaderboardPanel leaders={leaders} error={leadersError} onLeaderClick={openLeaderNode} />
        </div>
      );
    }
    if (id === 'signals') {
      return (
        <div className="flow-tile-scroll">
          <ActivityPanel activity={activity} error={activityError} onTickerClick={openSignalTicker} />
        </div>
      );
    }
    if (id === 'media') {
      return (
        <div className="flow-tile-scroll">
          <MediaPanel
            articles={news}
            error={newsError}
            onTickerClick={openSignalTicker}
            onMemberClick={openMemberAnywhere}
          />
        </div>
      );
    }
    if (id === 'path') {
      return (
        <div className="flow-tile-scroll">
          <PathPanel
            result={pathResult}
            error={pathError}
            onNodeClick={selectPathNode}
            isSelectable={(pid) => loadedIds.has(pid)}
          />
        </div>
      );
    }
    return null;
  };

  return (
    <div className="app">
      {initialUrl.embed ? (
        <div className="embed">
          <div className="embed-body">{renderTileBody(initialUrl.embed)}</div>
          <a className="embed-attr" href={window.location.pathname} target="_blank" rel="noreferrer">
            Baleen — watch Congress trade
          </a>
        </div>
      ) : (
        <>
          <CommandBar health={health} healthChecked={healthChecked} />
          {showTour && <OnboardingTour onDone={dismissTour} />}
          {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}

          <div className="app-body">
        <PanelRail
          open={railOpen}
          onToggle={handleRailToggle}
          counts={{ insights: 0, board: 0, leads: (signals?.uncovered.length ?? 0) + (signals?.suspicious.length ?? 0) + (signals?.momentum.length ?? 0), flow: flowCount, alerts: inbox.unread, reports: reportCount, leaders: leaders.length, signals: activityCount, media: newsCount, path: pathResult ? pathResult.nodes.length : 0 }}
        />
        <main className="graph-area">
          <TileDock
            layout={layout}
            enlarged={enlarged && openTiles.includes(enlarged) ? enlarged : null}
            onClose={(id) => setLayout((prev) => closeInLayout(prev, id))}
            onMove={(id, dir) => setLayout((prev) => moveSibling(prev, id, dir))}
            onSplit={(id, dir, panel) => setLayout((prev) => splitTile(prev, id, dir, panel))}
            onEnlarge={setEnlarged}
            renderBody={renderTileBody}
          />
          <div className="graph-status">
            {graph ? `${graph.node_count} nodes · ${graph.link_count} links` : graphError ? '' : 'Connecting…'}
            {graph?.truncated && graph.event_count
              ? ` · recent ${graph.node_count} of ${graph.event_count} events`
              : ''}
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
                similar={similar}
                onClose={() => setSelected(null)}
                onAlertClick={openAlertNode}
                onTickerClick={openSignalTicker}
                onMemberClick={openMemberAnywhere}
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
            ) : committeeId ? (
              <CommitteeDetail
                node={selected}
                card={committeeCard}
                cardError={committeeError}
                alerts={alerts}
                onClose={() => setSelected(null)}
                onAlertClick={openAlertNode}
                onMemberClick={openMemberAnywhere}
              />
            ) : (
              <NodeDetail node={selected} onClose={() => setSelected(null)} links={inspectorLinks} />
            )}
            <PathFromButton
              armed={pathFrom !== null}
              onArm={() => path.arm(selected)}
              onCancel={() => setPathFrom(null)}
            />
          </aside>
        )}
        <AlertStack alerts={alerts} onAlertClick={openAlertNode} />
          </div>
        </>
      )}
    </div>
  );
}
