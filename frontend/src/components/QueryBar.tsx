import { useEffect, useRef, useState } from 'react';

import { askQuestion } from '../api';
import { saveQuestion } from '../queryHistory';
import type { QueryResponse } from '../types';

interface Props {
  onResult: (result: QueryResponse | null) => void;
  onError: (message: string | null) => void;
  onFocusAsk?: () => void;
  externalAsk?: { text: string; nonce: number } | null;
  focusSignal?: number | null;
}

export default function QueryBar({ onResult, onError, onFocusAsk, externalAsk, focusSignal }: Props) {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const seenNonce = useRef<number | null>(null);
  const seenFocus = useRef<number | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function submit(q?: string) {
    const text = (q ?? question).trim();
    if (!text || busy) return;
    setQuestion(text);
    saveQuestion(text);
    setBusy(true);
    onError(null);
    try {
      onResult(await askQuestion(text));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
      onResult(null);
    } finally {
      setBusy(false);
    }
  }

  // Example questions (from the answer panel) submit through the same path.
  useEffect(() => {
    if (externalAsk && externalAsk.nonce !== seenNonce.current) {
      seenNonce.current = externalAsk.nonce;
      submit(externalAsk.text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalAsk]);

  // Command-palette trigger (Ctrl/⌘+K or `/` outside inputs): focus the box.
  useEffect(() => {
    if (focusSignal && focusSignal !== seenFocus.current) {
      seenFocus.current = focusSignal;
      inputRef.current?.focus();
    }
  }, [focusSignal]);

  return (
    <div className="query-bar">
      <input
        ref={inputRef}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
        onFocus={onFocusAsk}
        placeholder="Ask the graph… e.g. which sectors has Cassidy traded?"
      />
      <button onClick={() => submit()} disabled={busy || !question.trim()}>
        {busy ? '…' : 'Ask'}
      </button>
    </div>
  );
}
