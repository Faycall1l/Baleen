import { useCallback, useState } from 'react';

import AnswerPanel from './AnswerPanel';
import HealthDot from './HealthDot';
import QueryBar from './QueryBar';
import type { Health, QueryResponse } from '../types';

// Top command bar: brand, ask box + answer panel, health dot (extracted from
// App, H13). Owns the ask/query result state so App only holds the graph and
// feeds.
export default function CommandBar({ health }: { health: Health | null }) {
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [askSeed, setAskSeed] = useState<{ text: string; nonce: number } | null>(null);

  const onResult = useCallback((res: QueryResponse | null) => {
    setResult(res);
    setOpen(true);
  }, []);

  return (
    <header className="commandbar">
      <div className="brand">
        <img src="/baleen.svg" alt="Baleen logo" width={26} height={26} />
        <h1>Baleen</h1>
      </div>
      <div className="ask-wrap">
        <QueryBar
          onResult={onResult}
          onError={setError}
          onFocusAsk={() => setOpen(true)}
          externalAsk={askSeed}
        />
        {open && (
          <AnswerPanel
            result={result}
            error={error}
            onClose={() => setOpen(false)}
            onExample={(q) => setAskSeed({ text: q, nonce: Date.now() })}
          />
        )}
      </div>
      <HealthDot health={health} />
    </header>
  );
}
