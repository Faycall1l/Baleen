import type { QueryResponse } from '../types';

interface Props {
  result: QueryResponse | null;
  error: string | null;
  onClose: () => void;
  onExample: (question: string) => void;
}

const EXAMPLES = [
  'Which tickers did congressional traders buy most recently?',
  'Which sectors does the House Financial Services Committee oversee?',
  'Show unusual options flow by sector.',
];

export default function AnswerPanel({ result, error, onClose, onExample }: Props) {
  return (
    <div className="answer-panel">
      <button className="answer-close" onClick={onClose} aria-label="Dismiss answer">
        ×
      </button>
      {error && !result && <div className="error">{error}</div>}
      {!result && !error && (
        <div className="answer-empty">
          <div>
            Ask about the graph in plain English — the agent turns it into a
            validated Cypher query and explains the answer.
          </div>
          <div className="query-examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} className="query-example" onClick={() => onExample(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>
      )}
      {result && (
        <div className="query-result">
          <div className="answer">
            {result.has_data ? result.answer : 'No matching data in the graph yet.'}
          </div>
          <div className="meta">
            {result.allowed ? (
              <>
                <div>Generated Cypher:</div>
                <pre>{result.cypher}</pre>
                {result.sources.length > 0 && (
                  <div>
                    Sources:{' '}
                    {result.sources.map((s, i) => (
                      <a key={i} href={s} target="_blank" rel="noreferrer">
                        [{i + 1}]
                      </a>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="error">
                  Request blocked: {result.violations.join('; ')}
                </div>
                <pre>{result.cypher}</pre>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
