import AlertsPanel from './AlertsPanel';
import FlowTape from './FlowTape';
import LeaderboardPanel from './LeaderboardPanel';
import ReportsPanel from './ReportsPanel';
import SignalsPanel from './SignalsPanel';
import type { Alert, FlowFilters, FlowPrint, LeaderboardEntry, Report, SignalsBundle } from '../types';

export type Drawer = 'flow' | 'alerts' | 'reports' | 'leaders' | 'signals' | null;

interface Props {
  drawer: Drawer;
  onClose: () => void;
  flow: FlowPrint[];
  flowCount: number;
  flowError: string | null;
  flowFilters: FlowFilters;
  onFlowFiltersChange: (filters: FlowFilters) => void;
  onFlowRetry: () => void;
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
  digest: Report | null;
  reports: Report[];
  reportsError: string | null;
  onReportClick: (report: Report) => void;
  leaders: LeaderboardEntry[];
  leadersError: string | null;
  onLeaderClick: (leader: LeaderboardEntry) => void;
  signals: SignalsBundle | null;
  signalsError: string | null;
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
  digest,
  reports,
  reportsError,
  onReportClick,
  leaders,
  leadersError,
  onLeaderClick,
  signals,
  signalsError,
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
          error={alertsError}
          onAlertClick={onAlertClick}
          rule={alertRule}
          onRuleChange={onAlertRuleChange}
          query={alertQuery}
          onQueryChange={onAlertQueryChange}
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
  return null;
}
