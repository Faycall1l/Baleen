// Terminal keyboard map (R39): every shortcut in one table so the help
// overlay and the handlers can't drift apart.

export interface Shortcut {
  keys: string;
  action: string;
}

export const SHORTCUTS: Shortcut[] = [
  { keys: 'Ctrl/⌘ K or /', action: 'Focus the ask box' },
  { keys: '↑ ↓ + Enter', action: 'Move through search results and pick one' },
  { keys: 'Enter or Space', action: 'Open the focused timeline dot' },
  { keys: 'Esc', action: 'Restore tile, disarm path mode, or clear selection' },
  { keys: '?', action: 'Open this help' },
];
