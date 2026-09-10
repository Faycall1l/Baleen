import type { Report } from '../types';

interface Props {
  digest: Report | null;
  reports: Report[];
  error: string | null;
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function ReportsPanel({ digest, reports, error }: Props) {
  const briefs = reports.filter((r) => r.kind === 'brief');
  return (
    <>
      <h2>Reports</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && digest && (
        <div className="alert-item" key={`digest-${digest.id}`}>
          <div className="alert-title">
            <span className="alert-badge">digest</span> {digest.title}
          </div>
          <div className="alert-summary">{digest.body}</div>
          <div className="alert-meta">
            <span>{digest.created_at ? fmtDate(digest.created_at) : ''}</span>
          </div>
        </div>
      )}
      {!error && briefs.length === 0 && !digest && (
        <div className="empty-hint">
          No reports yet. Briefs are published automatically as events are
          processed; the daily digest appears here once a day has data.
        </div>
      )}
      {briefs.map((r) => (
        <div className="alert-item" key={r.id}>
          <div className="alert-title">
            <span className="alert-badge">brief</span> {r.title}
          </div>
          <div className="alert-summary">{r.body}</div>
          <div className="alert-meta">
            <span>{r.created_at ? fmtDate(r.created_at) : ''}</span>
          </div>
        </div>
      ))}
    </>
  );
}
