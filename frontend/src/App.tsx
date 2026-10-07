import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import AlertStack from './components/AlertStack';
import AlertsPanel from './components/AlertsPanel';
import LeaderboardPanel from './components/LeaderboardPanel';
import LeadsPanel from './components/LeadsPanel';
import ReportsPanel from './components/ReportsPanel';
import SignalsPanel from './components/SignalsPanel';
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
import { useGraphFeed } from './hooks/useGraphFeed';
import { useInspectorLinks } from './hooks/useInspectorLinks';
import { useCommittee } from './hooks/useCommittee';
import { usePerson } from './hooks/usePerson';
import { useTicker } from './hooks/useTicker';
import { usePollingTick } from './hooks/usePollingTick';
import { useSelection } from './hooks/useSelection';
import { loadOnboarded, saveOnboarded } from './onboarding';
import { personIdForNode } from './personView';
import { buildGraphUrl, parseGraphUrl } from './graphUrl';
import { loadHiddenTypes, loadTiles, saveHiddenTypes, saveTiles } from './uiPrefs';
import { closeTile, ensureTile, moveTile, toggleTile, type TileId } from './tileState';
import { soloHidden } from './typeFilters';
import { SCHEMA_LABELS } from './graph/schema';
import { daysAgoToISO, SCRUB_DAYS } from './timeScrub';
import { fetchGraph, fetchPath, fetchPerson, fetchTicker, fetchCommittee, type PathResponse } from './api';
import type { GraphData, GraphNode } from './types';

export default function App() {
  const { graph, graphError, graphStage, updatedAt, loadGraph, showEvents, setShowEvents } = useGraphFeed();
  const { selected, handleSelect, setSelected } = useSelection(graph);
  const { flow, count: flowCount, error: flowError, filters: flowFilters, setFilters: setFlowFilters, reload: reloadFlow } =
    useFlow();
  // Deep-link state (R46, tiles in P5): selection, hidden types, and open
  // panels encoded in the URL so any view is shareable. Read once on mount;
  // written back on every change below. An explicit tiles param beats prefs;
  // otherwise the workspace restores the last layout.
  const [initialUrl] = useState(() => parseGraphUrl(window.location.search));
  // Tile dock (P1): rail-opened panels share the main area. Persisted like
  // other workspace prefs; the board is permanent (see tileState).
  const [tiles, setTiles] = useState<TileId[]>(() => initialUrl.tiles ?? loadTiles());
  const [enlarged, setEnlarged] = useState<TileId | null>(null);
  useEffect(() => {
    saveTiles(tiles);
  }, [tiles]);
  // Type filters live here (not in TimelineBoard) so the URL writer below sees
  // the whole view state in one place.
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(
    () => new Set(initialUrl.hidden.length > 0 ? initialUrl.hidden : loadHiddenTypes()),
  );

  const toggleType = useCallback((label: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      saveHiddenTypes(next);
      return next;
    });
  }, []);

  const clearTypes = useCallback(() => {
    setHiddenTypes(() => {
      saveHiddenTypes([]);
      return new Set();
    });
  }, []);

  const soloType = useCallback((label: string) => {
    setHiddenTypes((prev) => {
      const next = soloHidden(SCHEMA_LABELS, label, prev);
      saveHiddenTypes(next);
      return next;
    });
  }, []);
  // Shortcut help (R39): `?` outside inputs toggles it.
  const [showHelp, setShowHelp] = useState(false);
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

  // Time scrubber (R47b): days-ago offset, null for live. The frozen
  // snapshot replaces the board data until cleared; live polls continue
  // underneath so jumping back is instant.
  const [scrubDaysAgo, setScrubDaysAgo] = useState<number | null>(null);
  const [asOfData, setAsOfData] = useState<GraphData | null>(null);
  useEffect(() => {
    if (scrubDaysAgo === null) {
      setAsOfData(null);
      return;
    }
    let cancelled = false;
    fetchGraph(500, undefined, undefined, daysAgoToISO(scrubDaysAgo, Date.now()))
      .then((data) => {
        if (!cancelled) setAsOfData(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [scrubDaysAgo]);

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
    healthChecked,
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

  // Select the deep-linked node once it loads (R46). Consumed on first
  // success — or on any manual selection — so clearing it stays cleared.
  // J3: when the node never loads (outside the window), resolve entity cards
  // directly — Politician/Ticker/Committee links open anywhere, so member
  // profiles are shareable even with no graph context loaded.
  const urlNodeConsumed = useRef(false);
  useEffect(() => {
    if (urlNodeConsumed.current || !graph || !initialUrl.node) return;
    if (selected) {
      urlNodeConsumed.current = true;
      return;
    }
    const node = graph.nodes.find((n) => n.id === initialUrl.node);
    if (node) {
      urlNodeConsumed.current = true;
      handleSelect(node);
      return;
    }
    // Not in the loaded window: resolve the card directly for entity links.
    urlNodeConsumed.current = true;
    const target = initialUrl.node;
    const sep = target.indexOf(':');
    const label = sep < 0 ? '' : target.slice(0, sep);
    const key = sep < 0 ? '' : target.slice(sep + 1);
    if (!key) return;
    let cancelled = false;
    const openCard = (title: string, props: Record<string, unknown>) => {
      if (!cancelled) handleSelect({ id: target, label, key, title, props });
    };
    if (label === 'Politician') {
      fetchPerson(key)
        .then((res) => openCard(res.person?.name ?? key, { id: key }))
        .catch(() => {});
    } else if (label === 'Ticker') {
      fetchTicker(key)
        .then((res) => openCard(res.ticker?.ticker ?? key, {}))
        .catch(() => {});
    } else if (label === 'Committee') {
      fetchCommittee(key)
        .then((res) => openCard(res.committee?.name ?? key, { id: key }))
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [graph, selected, initialUrl.node, handleSelect]);

  // Write the view state back to the URL on every change (R46, tiles P5).
  useEffect(() => {
    const url = buildGraphUrl({
      node: selected?.id ?? null,
      hidden: [...hiddenTypes],
      tiles,
    });
    window.history.replaceState(null, '', url);
  }, [selected?.id, hiddenTypes, tiles]);

  // Path mode (R53b): armed from any inspector via PathFromButton; the next
  // selected node completes the pair and opens the path tile. Declared
  // before the key handler below so Esc can disarm.
  const [pathFrom, setPathFrom] = useState<GraphNode | null>(null);
  const [pathResult, setPathResult] = useState<PathResponse | null>(null);
  const [pathError, setPathError] = useState<string | null>(null);

  // Esc restores an enlarged tile, disarms path mode, or clears the
  // inspector selection (R15/R16). `?` outside inputs opens help.
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
  }, [enlarged, showHelp, pathFrom]);

  // Alerts inbox (R8): unread watermark + rule/text filtering live here so
  // the rail badge and the tile share one source of truth.
  const inbox = useAlertsInbox(alerts, tiles.includes('alerts'));
  const { rules: alertRules, toggleRule: toggleAlertRule } = useAlertRules();

  const loadedIds = useMemo(
    () => new Set((graph?.nodes ?? []).map((n) => n.id)),
    [graph],
  );

  const selectPathNode = (id: string) => {
    const full = graph?.nodes.find((n) => n.id === id);
    if (full) handleSelect(full);
  };

  const handleBoardSelect = (node: GraphNode | null) => {
    if (node && pathFrom && node.id !== pathFrom.id) {
      const from = pathFrom;
      setPathFrom(null);
      setPathError(null);
      fetchPath(from.id, node.id)
        .then((res) => {
          setPathResult(res);
          setTiles((prev) => ensureTile(prev, 'path'));
        })
        .catch((err: unknown) => {
          setPathResult(null);
          setPathError(err instanceof Error ? err.message : 'Path fetch failed');
          setTiles((prev) => ensureTile(prev, 'path'));
        });
    } else if (node && pathFrom && node.id === pathFrom.id) {
      setPathFrom(null);
    }
    handleSelect(node);
  };

  // Cross-surface panel opener for the inspector links: null restores an
  // enlarged dock so the revealed node is visible; an id opens its tile.
  const openTile = useCallback((id: TileId | null) => {
    if (id === null) {
      setEnlarged(null);
      return;
    }
    setTiles((prev) => ensureTile(prev, id));
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

  // Committee view (R45b): same card pattern as politicians and tickers.
  const committeeId =
    selected?.label === 'Committee'
      ? (typeof selected.props.id === 'string' && selected.props.id) ||
        selected.id.split(':').slice(1).join(':') ||
        null
      : null;
  const { card: committeeCard, error: committeeError } = useCommittee(committeeId);

  // Cross-surface jump (R36): filter the tape to this ticker and make
  // sure its tile is open.
  const viewTickerInTape = (ticker: string) => {
    setFlowFilters((f) => ({ ...f, tickers: [ticker] }));
    setTiles((prev) => ensureTile(prev, 'flow'));
  };

  // Rail toggle (P5): every tab docks its tile, board included.
  const handleRailToggle = (key: RailKey) => {
    setTiles((prev) => toggleTile(prev, key));
  };
  const railOpen: RailKey[] = [...tiles];

  const renderTileBody = (id: TileId) => {
    const openSignalTicker = (t: string) => {
      if (!openTickerNode(t)) viewTickerInTape(t);
    };
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
          <SignalsPanel signals={signals} error={signalsError} onTickerClick={openSignalTicker} />
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
      <CommandBar health={health} healthChecked={healthChecked} />
      {showTour && <OnboardingTour onDone={dismissTour} />}
      {showHelp && <ShortcutsHelp onClose={() => setShowHelp(false)} />}

      <div className="app-body">
        <PanelRail
          open={railOpen}
          onToggle={handleRailToggle}
          counts={{ board: 0, leads: (signals?.uncovered.length ?? 0) + (signals?.suspicious.length ?? 0) + (signals?.momentum.length ?? 0), flow: flowCount, alerts: inbox.unread, reports: reportCount, leaders: leaders.length, signals: signalsCount, path: pathResult ? pathResult.nodes.length : 0 }}
        />
        <main className="graph-area">
          <TileDock
            tiles={tiles}
            enlarged={enlarged && tiles.includes(enlarged) ? enlarged : null}
            onClose={(id) => setTiles((prev) => closeTile(prev, id))}
            onMove={(id, dir) => setTiles((prev) => moveTile(prev, id, dir))}
            onEnlarge={setEnlarged}
            onOpen={(id) => setTiles((prev) => ensureTile(prev, id))}
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
                onClose={() => setSelected(null)}
                onAlertClick={openAlertNode}
                onTickerClick={(t) => {
                  if (!openTickerNode(t)) viewTickerInTape(t);
                }}
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
                onMemberClick={openPoliticianNode}
              />
            ) : (
              <NodeDetail node={selected} onClose={() => setSelected(null)} links={inspectorLinks} />
            )}
            <PathFromButton
              armed={pathFrom !== null}
              onArm={() => {
                setPathResult(null);
                setPathError(null);
                setPathFrom(selected);
              }}
              onCancel={() => setPathFrom(null)}
            />
          </aside>
        )}
        <AlertStack alerts={alerts} onAlertClick={openAlertNode} />
      </div>
    </div>
  );
}
