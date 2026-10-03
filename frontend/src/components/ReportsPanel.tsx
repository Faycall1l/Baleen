import { groupBriefsByDay } from '../reportsView';
import { downloadMarkdown, reportFilename, reportToMarkdown } from '../reportExport';
import type { Report } from '../types';

interface Props {
  digest: Report | null;
  reports: Report[];
  error: string | null;
  onReportClick?: (report: Report) => void;
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function BriefRow({ report, onReportClick }: { report: Report; onReportClick?: (report: Report) => void }) {
  return (
    <div
      className={`alert-item${onReportClick ? ' clickable' : ''}`}
      onClick={onReportClick ? () => onReportClick(report) : undefined}
    >
      <div className="alert-title">{report.title}</div>
      <div className="alert-summary">{report.body}</div>
      <div className="alert-meta">
        <span>{report.created_at ? fmtDate(report.created_at) : ''}</span>
        {(report.graph_refs ?? []).map((ref) => (
          <span key={ref} className="ref-chip">
            {ref}
          </span>
        ))}
        <button
          type="button"
          className="node-link"
          onClick={(e) => {
            e.stopPropagation();
            downloadMarkdown(reportFilename(report), reportToMarkdown(report));
          }}
        >
          Export .md
        </button>
      </div>
    </div>
  );
}

export default function ReportsPanel({ digest, reports, error, onReportClick }: Props) {
  const briefs = reports.filter((r) => r.kind === 'brief');
  const days = groupBriefsByDay(briefs);
  return (
    <>
      <h2>Reports</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && digest && (
        <div className="digest-card" key={`digest-${digest.id}`}>
          <div className="digest-kicker">DAILY DIGEST · {digest.period ?? ''}</div>
          <div className="digest-title">{digest.title}</div>
          <div className="alert-summary">{digest.body}</div>
          <div className="alert-meta">
            <button
              type="button"
              className="node-link"
              onClick={() => downloadMarkdown(reportFilename(digest), reportToMarkdown(digest))}
            >
              Export .md
            </button>
          </div>
        </div>
      )}
      {!error && briefs.length === 0 && !digest && (
        <div className="empty-hint">
          No reports yet. Briefs are published automatically as events are
          processed; the daily digest appears here once a day has data.
        </div>
      )}
      {days.map((day) => (
        <section key={day.day}>
          <h3 className="brief-day">
            Briefs · {day.label}
            <span className="brief-day-count">{day.items.length}</span>
          </h3>
          {day.items.map((r) => (
            <BriefRow key={r.id} report={r} onReportClick={onReportClick} />
          ))}
        </section>
      ))}
    </>
  );
}
