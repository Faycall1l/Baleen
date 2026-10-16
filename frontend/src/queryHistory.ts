// Asked-question history for the command bar.
//
// Terminal users re-ask and refine: the last questions persist locally,
// most-recent first, deduped and capped, and surface as one-click chips in
// the empty answer panel. No backend, no account — localStorage only.

export const HISTORY_KEY = 'baleen.query.history';
export const HISTORY_CAP = 20;

export function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((q): q is string => typeof q === 'string' && q.length > 0);
  } catch {
    return [];
  }
}

export function saveQuestion(question: string): void {
  const text = question.trim();
  if (!text) return;
  try {
    const next = [text, ...loadHistory().filter((q) => q !== text)].slice(0, HISTORY_CAP);
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  } catch {
    /* private mode etc. — history just doesn't persist */
  }
}
