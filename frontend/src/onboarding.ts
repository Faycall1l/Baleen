// First-run onboarding: a four-step tour of the terminal.
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
    id: 'leads',
    title: '1 · Start with story leads',
    body: 'The first tile ranks today’s congressional-trading leads: unsupervised sector trades, market-beating filings, congress flow. Click any ticker to open its evidence.',
  },
  {
    id: 'ask',
    title: '2 · Ask in plain English',
    body: 'The top bar takes questions like “which sectors has Cassidy traded?” The agent writes validated read-only queries and answers with a grounded table — every cell traceable, never invented.',
  },
  {
    id: 'alerts',
    title: '3 · Work the alerts inbox',
    body: 'Oversight-sector trades, $1M+ insider buys, and options sweeps land here with rule chips and text search. The rail badge counts unread; opening the inbox marks them seen.',
  },
  {
    id: 'tiles',
    title: '4 · Arrange your desk',
    body: 'Every rail tab docks a tile beside the others. Split any tile vertically or horizontally for side-by-side views, reorder with ← →, enlarge to focus, close what you don’t need — the layout persists, and any view copies as a share link.',
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
