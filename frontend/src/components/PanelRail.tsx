import type { TileId } from '../tileState';

export type RailKey = TileId;

const ITEMS: { key: RailKey; label: string }[] = [
  { key: 'board', label: 'Board' },
  { key: 'leads', label: 'Leads' },
  { key: 'flow', label: 'Flow' },
  { key: 'alerts', label: 'Alerts' },
  { key: 'reports', label: 'Reports' },
  { key: 'leaders', label: 'Leaders' },
  { key: 'signals', label: 'Activity' },
];

// Left rail of panel toggles (extracted from App, H12). Counts come from the
// feed hook; a zero count shows no badge. `open` is every currently visible
// panel (tiles on wide screens, the overlay drawer on narrow ones); the App
// owns toggle semantics.
export default function PanelRail({
  open,
  onToggle,
  counts,
}: {
  open: RailKey[];
  onToggle: (key: RailKey) => void;
  counts: Record<RailKey, number>;
}) {
  const openSet = new Set(open);
  return (
    <nav className="rail" aria-label="Panels">
      {ITEMS.map(({ key, label }) => (
        <button
          key={key}
          className={`rail-btn${openSet.has(key) ? ' active' : ''}`}
          onClick={() => onToggle(key)}
          title={label}
          aria-expanded={openSet.has(key)}
        >
          <span className="rail-label">{label}</span>
          {counts[key] > 0 && <span className="rail-badge">{counts[key]}</span>}
        </button>
      ))}
    </nav>
  );
}
