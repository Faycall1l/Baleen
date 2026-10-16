import { useMemo } from 'react';

import type { ActivityBundle, LeaderboardEntry, SignalsBundle } from '../types';

interface Props {
  signals: SignalsBundle | null;
  leaders: LeaderboardEntry[];
  activity: ActivityBundle | null;
  unreadAlerts: number;
  onTickerClick: (ticker: string) => void;
}

const fmt = (n: number) => (Math.abs(n) >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : Math.abs(n) >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n)));
const pct = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(1)}%`;

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'up' | 'down' }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

function Card({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="chart-card">
      <header>
        <h3>{title}</h3>
        {sub && <span>{sub}</span>}
      </header>
      {children}
    </section>
  );
}

/** Insights: KPI strip plus four pure-SVG/CSS charts over the same feeds the
 * other tiles use (no extra requests, no chart dependency). */
export default function InsightsPanel({ signals, leaders, activity, unreadAlerts, onTickerClick }: Props) {
  const momentum = useMemo(
    () => (signals?.momentum ?? []).filter((m) => m.ticker).slice(0, 8),
    [signals],
  );
  const options = useMemo(
    () =>
      (signals?.options_meter ?? [])
        .filter((o) => o.ticker)
        .map((o) => ({ ...o, c: o.call_vol ?? 0, p: o.put_vol ?? 0 }))
        .filter((o) => o.c + o.p > 0)
        .slice(0, 8),
    [signals],
  );
  const lead = useMemo(
    () => leaders.filter((l) => l.excess_pct != null).slice(0, 8),
    [leaders],
  );
  const movers = useMemo(() => (activity?.movers ?? []).filter((m) => m.ticker).slice(0, 8), [activity]);

  if (!signals && !activity && !leaders.length) {
    return <div className="empty-hint">Loading insights…</div>;
  }

  const buys = (signals?.momentum ?? []).reduce((a, m) => a + m.buys, 0);
  const sells = (signals?.momentum ?? []).reduce((a, m) => a + m.sells, 0);
  const calls = (signals?.options_meter ?? []).reduce((a, o) => a + (o.call_vol ?? 0), 0);
  const puts = (signals?.options_meter ?? []).reduce((a, o) => a + (o.put_vol ?? 0), 0);
  const med = median(leaders.map((l) => l.excess_pct).filter((x): x is number => x != null));
  const beating = leaders.filter((l) => (l.excess_pct ?? 0) > 0).length;
  const mMax = Math.max(1, ...momentum.map((m) => Math.max(m.buys, m.sells)));
  const lMax = Math.max(1, ...lead.map((l) => Math.abs(l.excess_pct ?? 0)));
  const vMax = Math.max(1, ...movers.map((m) => Math.max(m.recent, m.prior)));

  return (
    <div className="insights">
      <div className="kpi-strip">
        <Kpi
          label="Congress buy / sell"
          value={sells ? (buys / sells).toFixed(2) : buys ? '∞' : '–'}
          hint={`${buys} buys · ${sells} sells`}
          tone={buys >= sells ? 'up' : 'down'}
        />
        <Kpi
          label="Options call / put"
          value={puts ? (calls / puts).toFixed(2) : calls ? '∞' : '–'}
          hint={`${fmt(calls)} calls · ${fmt(puts)} puts`}
          tone={calls >= puts ? 'up' : 'down'}
        />
        <Kpi
          label="Median excess return"
          value={med == null ? '–' : pct(med)}
          hint={leaders.length ? `${beating}/${leaders.length} beat the market` : undefined}
          tone={med == null ? undefined : med >= 0 ? 'up' : 'down'}
        />
        <Kpi label="Insider clusters" value={String(signals?.clusters.length ?? 0)} hint="tickers with multi-insider buys" />
        <Kpi label="Off-committee trades" value={String(signals?.uncovered.length ?? 0)} hint="sectors nobody oversees" />
        <Kpi label="Unread alerts" value={String(unreadAlerts)} tone={unreadAlerts ? 'down' : undefined} />
      </div>

      <Card title="Congress momentum" sub="buys vs sells per ticker">
        {momentum.length === 0 ? (
          <div className="empty-hint">No congressional trades in window.</div>
        ) : (
          momentum.map((m) => (
            <button key={m.ticker} className="bar-row" onClick={() => onTickerClick(m.ticker as string)}>
              <span className="bar-label">{m.ticker}</span>
              <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="bar-svg" aria-hidden>
                <line x1="50" x2="50" y1="0" y2="10" className="axis" />
                <rect x={50 - (m.sells / mMax) * 50} y="1" width={(m.sells / mMax) * 50} height="8" className="neg" />
                <rect x="50" y="1" width={(m.buys / mMax) * 50} height="8" className="pos" />
              </svg>
              <span className="bar-val">{m.buys}/{m.sells}</span>
            </button>
          ))
        )}
      </Card>

      <Card title="Options flow" sub="call vs put volume">
        {options.length === 0 ? (
          <div className="empty-hint">No options prints yet.</div>
        ) : (
          options.map((o) => (
            <button key={o.ticker} className="bar-row" onClick={() => onTickerClick(o.ticker as string)}>
              <span className="bar-label">{o.ticker}</span>
              <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="bar-svg" aria-hidden>
                <rect x="0" y="1" width={(o.c / (o.c + o.p)) * 100} height="8" className="pos" />
                <rect x={(o.c / (o.c + o.p)) * 100} y="1" width={(o.p / (o.c + o.p)) * 100} height="8" className="neg" />
              </svg>
              <span className="bar-val">{fmt(o.c + o.p)}</span>
            </button>
          ))
        )}
      </Card>

      <Card title="Beating the market" sub="excess return since filing">
        {lead.length === 0 ? (
          <div className="empty-hint">No scored trades yet.</div>
        ) : (
          lead.map((l, i) => {
            const v = l.excess_pct as number;
            const w = (Math.abs(v) / lMax) * 50;
            return (
              <button key={`${l.politician}-${l.ticker}-${i}`} className="bar-row" onClick={() => onTickerClick(l.ticker)}>
                <span className="bar-label" title={l.politician}>{l.ticker}</span>
                <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="bar-svg" aria-hidden>
                  <line x1="50" x2="50" y1="0" y2="10" className="axis" />
                  <rect x={v >= 0 ? 50 : 50 - w} y="1" width={w} height="8" className={v >= 0 ? 'pos' : 'neg'} />
                </svg>
                <span className="bar-val">{pct(v)}</span>
              </button>
            );
          })
        )}
      </Card>

      <Card title="Activity movers" sub="recent vs prior window">
        {movers.length === 0 ? (
          <div className="empty-hint">No movers.</div>
        ) : (
          movers.map((m) => (
            <button key={m.ticker} className="bar-row" onClick={() => onTickerClick(m.ticker as string)}>
              <span className="bar-label">{m.ticker}</span>
              <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="bar-svg" aria-hidden>
                <rect x="0" y="0.5" width={(m.prior / vMax) * 100} height="4" className="dim" />
                <rect x="0" y="5.5" width={(m.recent / vMax) * 100} height="4" className="pos" />
              </svg>
              <span className="bar-val">{m.delta > 0 ? `+${m.delta}` : m.delta}</span>
            </button>
          ))
        )}
      </Card>
    </div>
  );
}
