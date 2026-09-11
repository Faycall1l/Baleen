import { useState } from 'react';

import { askQuestion } from '../api';
import type { QueryResponse } from '../types';

interface Props {
  onResult: (result: QueryResponse | null) => void;
}

const EXAMPLES = [
  'Which tickers did congressional traders buy most recently?',
  'Which sectors does the House Financial Services Committee oversee?',
  'Show unusual options flow by sector.',
];

export default function QueryBar({ onResult }: Props) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(q?: string) {
    const text = (q ?? question).trim();
    if (!text || busy) return;
    setQuestion(text);
    setBusy(true);
    setError(null);
    try {
      onResult(await askQuestion(text));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      onResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="query-bar">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Ask the graph… e.g. which sectors has Cassidy traded?"
        />
        <button onClick={() => submit()} disabled={busy || !question.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </div>
      <div className="query-examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} className="query-example" onClick={() => submit(ex)} disabled={busy}>
            {ex}
          </button>
        ))}
      </div>
      {error && (
        <div className="query-result">
          <div className="error">{error}</div>
        </div>
      )}
    </>
  );
}
