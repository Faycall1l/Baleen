// First-run onboarding: a four-step tour of the terminal (R11).
//
// Research is blunt about the failure mode here: knowledge-graph tools
// confront new users with scope uncertainty (what's in here?), ontology
// opacity (what do these shapes mean?), and query incapacity (what can I
// even ask?). This tour answers all three before the user has to act:
// where the flow is, how to ask, where alerts land, what the graph shows.

export const ONBOARDING_KEY = 'baleen.onboarded';

export interface OnboardingStep {
  id: string;
  title: string;
  body: string;
}

export const ONBOARDING_STEPS: OnboardingStep[] = [
  {
    id: 'flow',
    title: '1 · Read the tape',
    body: 'The left column is the unusual-options tape: every print ranked by volume over open interest. Filter by ticker, calls or puts, and minimum ratio. Green ratios are bullish flow, red are bearish.',
  },
  {
    id: 'ask',
    title: '2 · Ask the graph',
    body: 'The top bar takes plain English. The agent writes validated read-only Cypher, runs it, and answers with a grounded table — every cell traceable to the graph, never invented.',
  },
  {
    id: 'alerts',
    title: '3 · Work the alerts inbox',
    body: 'Oversight-sector trades, $1M+ insider buys, and options sweeps land here with rule chips and text search. The rail badge counts unread; opening the inbox marks them seen.',
  },
  {
    id: 'graph',
    title: '4 · Explore the graph',
    body: 'The canvas shows recent events and their tickers, politicians, committees, and sectors. Click a node to focus its neighborhood and open the inspector; switch to Schema for the type overview.',
  },
];

export function loadOnboarded(): boolean {
  try {
    return localStorage.getItem(ONBOARDING_KEY) === '1';
  } catch {
    return false;
  }
}

export function saveOnboarded(): void {
  try {
    localStorage.setItem(ONBOARDING_KEY, '1');
  } catch {
    /* private mode etc. — the tour just shows again next visit */
  }
}
