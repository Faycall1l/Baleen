import AlertsPanel from './AlertsPanel';
import LeaderboardPanel from './LeaderboardPanel';
import ReportsPanel from './ReportsPanel';
import SignalsPanel from './SignalsPanel';
import type { Alert, LeaderboardEntry, Report, SignalsBundle } from '../types';

export type Drawer = 'alerts' | 'reports' | 'leaders' | 'signals' | null;

interface Props {
  drawer: Drawer;
  onClose: () => void;
  alerts: Alert[];
  alertsError: string | null;
  onAlertClick: (alert: Alert) => void;
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
  alerts,
  alertsError,
  onAlertClick,
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
  if (drawer === 'alerts') {
    return (
      <Shell onClose={onClose}>
        <AlertsPanel alerts={alerts} error={alertsError} onAlertClick={onAlertClick} />
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
