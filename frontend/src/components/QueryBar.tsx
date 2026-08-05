import { useState } from 'react';

import { askQuestion } from '../api';
import type { QueryResponse } from '../types';

interface Props {
  onResult: (result: QueryResponse | null) => void;
}

export default function QueryBar({ onResult }: Props) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      onResult(await askQuestion(q));
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
        <button onClick={submit} disabled={busy || !question.trim()}>
          {busy ? '…' : 'Ask'}
        </button>
      </div>
      {error && (
        <div className="query-result">
          <div className="error">{error}</div>
        </div>
      )}
    </>
  );
}
