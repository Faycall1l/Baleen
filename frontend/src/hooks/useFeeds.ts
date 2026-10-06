import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fetchAlerts,
  fetchDigest,
  fetchHealth,
  fetchLeaderboard,
  fetchReports,
  fetchSignals,
} from '../api';
import type { Alert, Health, LeaderboardEntry, Report, SignalsBundle } from '../types';

// Side-panel feeds: alerts, reports + digest, leaderboard, signals, plus the
// health dot (extracted from App, H10). The graph is polled separately by
// useGraphFeed; App owns the shared 30s tick that drives both.
export function useFeeds() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [digest, setDigest] = useState<Report | null>(null);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);
  const [leadersError, setLeadersError] = useState<string | null>(null);
  const [signals, setSignals] = useState<SignalsBundle | null>(null);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  // First check completed (success or failure): before this the pill must
  // not claim OFFLINE — nothing has been measured yet.
  const [healthChecked, setHealthChecked] = useState(false);

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

  // Refs mirror state so the stream can send fresh cursors on reconnect.
  const reportsRef = useRef<Report[]>([]);
  reportsRef.current = reports;
  const alertsRef = useRef<Alert[]>([]);
  alertsRef.current = alerts;

  useEffect(() => {
    // A hidden tab skips health polls like every other tick (R7).
    const checkHealth = () => {
      if (document.hidden) return;
      fetchHealth()
        .then((h) => {
          setHealth(h);
          setHealthChecked(true);
        })
        .catch(() => setHealthChecked(true));
    };
    checkHealth();
    const id = setInterval(checkHealth, 15_000);
    return () => clearInterval(id);
  }, []);

  // Live stream: append newly published reports/alerts as they land. The
  // 30s poll stays as the history loader and fallback; appends dedupe by id
  // so races between the two are harmless. Manual reconnect (with fresh
  // cursors) instead of EventSource's built-in retry, which would replay
  // from stale cursors.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One stable callback for the 30s tick so App's effect deps stay flat.
  const loadAll = useCallback(() => {
    loadAlerts();
    loadReports();
    loadLeaders();
    loadSignals();
  }, [loadAlerts, loadReports, loadLeaders, loadSignals]);

  const briefs = reports.filter((r) => r.kind === 'brief');
  const reportCount = briefs.length + (digest ? 1 : 0);
  const signalsCount = signals
    ? signals.momentum.length +
      signals.clusters.length +
      signals.options_meter.length +
      signals.suspicious.length +
      signals.uncovered.length
    : 0;

  return {
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
  };
}
