import AlertsPanel from './AlertsPanel';
import FlowTape from './FlowTape';
import LeaderboardPanel from './LeaderboardPanel';
import PathPanel from './PathPanel';
import ReportsPanel from './ReportsPanel';
import SignalsPanel from './SignalsPanel';
import type { Alert, FlowFilters, FlowPrint, LeaderboardEntry, Report, SignalsBundle } from '../types';
import type { AlertRule, PathResponse } from '../api';

export type Drawer = 'flow' | 'alerts' | 'reports' | 'leaders' | 'signals' | 'path' | null;

interface Props {
  drawer: Drawer;
  onClose: () => void;
  flow: FlowPrint[];
  flowCount: number;
  flowError: string | null;
  flowFilters: FlowFilters;
  onFlowFiltersChange: (filters: FlowFilters) => void;
  onFlowRetry: () => void;
  onFlowSelectTicker: (ticker: string) => void;
  alerts: Alert[];
  alertsTotal: number;
  alertsRules: string[];
  alertsError: string | null;
  onAlertClick: (alert: Alert) => void;
  alertRule: string;
  onAlertRuleChange: (rule: string) => void;
  alertQuery: string;
  onAlertQueryChange: (query: string) => void;
  alertSeenMax: number;
  alertRules: AlertRule[];
  onAlertRuleToggle: (name: string, active: boolean) => void;
  digest: Report | null;
  reports: Report[];
  reportsError: string | null;
  onReportClick: (report: Report) => void;
  leaders: LeaderboardEntry[];
  leadersError: string | null;
  onLeaderClick: (leader: LeaderboardEntry) => void;
  signals: SignalsBundle | null;
  signalsError: string | null;
  pathResult: PathResponse | null;
  pathError: string | null;
  onPathNodeClick: (id: string) => void;
  isPathNodeSelectable: (id: string) => boolean;
}

function Shell({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="drawer">
      <button className="drawer-close" onClick={onClose} aria-label="Close panel">
        ×
      </button>
      {children}
    </div>
  );
}

// Right-side drawers, one per rail tab (extracted from App, H3). The repeated
// drawer shell collapses into one wrapper.
export default function PanelDrawers({
  drawer,
  onClose,
  flow,
  flowCount,
  flowError,
  flowFilters,
  onFlowFiltersChange,
  onFlowRetry,
  onFlowSelectTicker,
  alerts,
  alertsTotal,
  alertsRules,
  alertsError,
  onAlertClick,
  alertRule,
  onAlertRuleChange,
  alertQuery,
  onAlertQueryChange,
  alertSeenMax,
  alertRules,
  onAlertRuleToggle,
  digest,
  reports,
  reportsError,
  onReportClick,
  leaders,
  leadersError,
  onLeaderClick,
  signals,
  signalsError,
  pathResult,
  pathError,
  onPathNodeClick,
  isPathNodeSelectable,
}: Props) {
  if (drawer === 'flow') {
    return (
      <Shell onClose={onClose}>
        <FlowTape
          flow={flow}
          count={flowCount}
          error={flowError}
          filters={flowFilters}
          onFiltersChange={onFlowFiltersChange}
          onRetry={onFlowRetry}
          onSelectTicker={onFlowSelectTicker}
        />
      </Shell>
    );
  }
  if (drawer === 'alerts') {
    return (
      <Shell onClose={onClose}>
        <AlertsPanel
          alerts={alerts}
          total={alertsTotal}
          rules={alertsRules}
          ruleStates={alertRules}
          error={alertsError}
          onAlertClick={onAlertClick}
          rule={alertRule}
          onRuleChange={onAlertRuleChange}
          query={alertQuery}
          onQueryChange={onAlertQueryChange}
          onToggleRule={onAlertRuleToggle}
          seenMax={alertSeenMax}
        />
      </Shell>
    );
  }
  if (drawer === 'reports') {
    return (
      <Shell onClose={onClose}>
        <ReportsPanel digest={digest} reports={reports} error={reportsError} onReportClick={onReportClick} />
      </Shell>
    );
  }
  if (drawer === 'leaders') {
    return (
      <Shell onClose={onClose}>
        <LeaderboardPanel leaders={leaders} error={leadersError} onLeaderClick={onLeaderClick} />
      </Shell>
    );
  }
  if (drawer === 'signals') {
    return (
      <Shell onClose={onClose}>
        <SignalsPanel signals={signals} error={signalsError} />
      </Shell>
    );
  }
  if (drawer === 'path') {
    return (
      <Shell onClose={onClose}>
        <PathPanel
          result={pathResult}
          error={pathError}
          onNodeClick={onPathNodeClick}
          isSelectable={isPathNodeSelectable}
        />
      </Shell>
    );
  }
  return null;
}
