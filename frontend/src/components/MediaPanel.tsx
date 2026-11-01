import { fmtAge } from '../mediaView';
import type { NewsArticle } from '../types';

interface Props {
  articles: NewsArticle[];
  error: string | null;
  onTickerClick: (ticker: string) => void;
  onMemberClick: (pid: string) => void;
}

/** Media watch: headlines from the keyless RSS/Atom monitor.
 *
 * Articles are stored independently of event extraction, so a story stays
 * visible even when it contains no typed trade event. Mentions are
 * deterministic (cashtags and exact roster names) and tap through to the same
 * inspector the rest of the workspace uses.
 */
export default function MediaPanel({ articles, error, onTickerClick, onMemberClick }: Props) {
  return (
    <>
      <h2>Media watch</h2>
      {error && (
        <div className="empty-hint">
          <div className="error">{error}</div>
        </div>
      )}
      {!error && articles.length === 0 && (
        <div className="empty-hint">
          No monitored headlines yet. The keyless feeds poll every 15 minutes — SEC, Federal Reserve, CNBC and
          Politico.
        </div>
      )}
      {articles.map((article) => (
        <article key={article.id} className="alert-item media-item">
          <div className="alert-meta media-meta">
            <span className="media-outlet">{article.outlet}</span>
            <span>{fmtAge(article.published_at)}</span>
          </div>
          <a className="alert-title media-title" href={article.url} target="_blank" rel="noreferrer">
            {article.title}
          </a>
          {article.summary && <div className="alert-summary">{article.summary}</div>}
          {article.mentions.length > 0 && (
            <div className="alert-meta media-mentions">
              {article.mentions.map((mention) =>
                mention.kind === 'ticker' ? (
                  <button
                    key={`${mention.kind}:${mention.id}`}
                    className="ref-chip media-chip"
                    onClick={() => onTickerClick(mention.id)}
                    title={`Show ${mention.label} in the inspector`}
                  >
                    {mention.label}
                  </button>
                ) : (
                  <button
                    key={`${mention.kind}:${mention.id}`}
                    className="ref-chip media-chip"
                    onClick={() => onMemberClick(mention.id)}
                    title={`Open ${mention.label}`}
                  >
                    {mention.label}
                  </button>
                ),
              )}
            </div>
          )}
        </article>
      ))}
    </>
  );
}
