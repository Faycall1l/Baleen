import type { SignalsBundle } from '../types';

interface Props {
  signals: SignalsBundle | null;
  error: string | null;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="alert-item">
      <div className="alert-title">
        <span className="alert-badge">{title}</span>
      </div>
      <div className="alert-summary">{children}</div>
    </div>
  );
}

export default function SignalsPanel({ signals, error }: Props) {
  return (
    <>
      <h2>Signals</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && !signals && <div className="empty-hint">Loading signals…</div>}
      {signals && (
        <>
          <Section title="congress momentum">
            {signals.momentum.length === 0 && 'No trades in window.'}
            {signals.momentum.slice(0, 5).map((m) => (
              <div key={m.ticker}>
                <b>{m.ticker}</b> — {m.buys} buys / {m.sells} sells
                {m.names?.length ? ` (${m.names.join(', ')})` : ''}
              </div>
            ))}
          </Section>
          <Section title="insider clusters">
            {signals.clusters.length === 0 && 'No multi-insider buys right now.'}
            {signals.clusters.map((c) => (
              <div key={c.ticker}>
                <b>{c.ticker}</b> — {c.insiders.length} insiders buying
                ({c.insiders.join(', ')})
              </div>
            ))}
          </Section>
          <Section title="options meter">
            {signals.options_meter.length === 0 && 'No unusual flow in window.'}
            {signals.options_meter.slice(0, 5).map((o) => (
              <div key={o.ticker}>
                <b>{o.ticker}</b> — {(o.call_vol ?? 0).toLocaleString()} calls /{' '}
                {(o.put_vol ?? 0).toLocaleString()} puts
              </div>
            ))}
          </Section>
          <Section title="worth a look">
            {signals.suspicious.length === 0 && 'Nothing beating the market hard.'}
            {signals.suspicious.slice(0, 5).map((s, i) => (
              <div key={`${s.who}-${s.ticker}-${i}`}>
                {s.who} · <b>{s.ticker}</b> +{Number(s.excess_pct).toFixed(1)}% vs
                market{' '}
                <a href={s.source_url} target="_blank" rel="noreferrer">
                  filing ↗
                </a>
              </div>
            ))}
          </Section>
          <Section title="uncovered bets">
            {signals.uncovered.length === 0 && 'Every traded sector is overseen.'}
            {signals.uncovered.slice(0, 5).map((u, i) => (
              <div key={`${u.politician}-${u.sector}-${i}`}>
                {u.politician} trading <b>{u.sector}</b> with no committee
                oversight ({u.symbols?.join(', ')})
              </div>
            ))}
          </Section>
        </>
      )}
    </>
  );
}
