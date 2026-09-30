import { useCallback, useEffect, useState } from 'react';

import AnswerPanel from './AnswerPanel';
import HealthDot from './HealthDot';
import QueryBar from './QueryBar';
import { loadHistory } from '../queryHistory';
import type { Health, QueryResponse } from '../types';

// Top command bar: brand, ask box + answer panel, health dot (extracted from
// App, H13). Owns the ask/query result state so App only holds the graph and
// feeds.
export default function CommandBar({ health }: { health: Health | null }) {
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [askSeed, setAskSeed] = useState<{ text: string; nonce: number } | null>(null);
  const [focusSignal, setFocusSignal] = useState<number | null>(null);

  const onResult = useCallback((res: QueryResponse | null) => {
    setResult(res);
    setOpen(true);
  }, []);

  // Terminal palette trigger: Ctrl/⌘+K anywhere, or `/` when not typing,
  // focuses the ask box (and opens the panel so history is one click away).
  // Esc closes the answer panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      if (e.key === 'Escape') {
        setOpen(false);
        return;
      }
      if (((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') || (e.key === '/' && !typing)) {
        e.preventDefault();
        setFocusSignal(Date.now());
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
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
          focusSignal={focusSignal}
        />
        {open && (
          <AnswerPanel
            result={result}
            error={error}
            history={loadHistory()}
            onClose={() => setOpen(false)}
            onExample={(q) => setAskSeed({ text: q, nonce: Date.now() })}
          />
        )}
      </div>
      <HealthDot health={health} />
    </header>
  );
}
