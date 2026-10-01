// Terminal keyboard map (R39): every shortcut in one table so the help
// overlay and the handlers can't drift apart.

export interface Shortcut {
  keys: string;
  action: string;
}

export const SHORTCUTS: Shortcut[] = [
  { keys: 'Ctrl/⌘ K or /', action: 'Focus the ask box' },
  { keys: '↑ ↓ + Enter', action: 'Move through search results and pick one' },
  { keys: '← → ↑ ↓', action: 'Move canvas selection node to node (canvas focused)' },
  { keys: '+ / -', action: 'Zoom the canvas in and out' },
  { keys: 'Esc', action: 'Close panel, drawer, or selection' },
  { keys: '?', action: 'Open this help' },
];
