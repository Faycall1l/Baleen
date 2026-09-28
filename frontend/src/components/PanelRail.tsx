import type { Drawer } from './PanelDrawers';

export type RailKey = Exclude<Drawer, null>;

const ITEMS: { key: RailKey; label: string }[] = [
  { key: 'alerts', label: 'Alerts' },
  { key: 'reports', label: 'Reports' },
  { key: 'leaders', label: 'Board' },
  { key: 'signals', label: 'Signals' },
];

// Left rail of panel toggles (extracted from App, H12). Counts come from the
// feed hook; a zero count shows no badge.
export default function PanelRail({
  drawer,
  onSelect,
  counts,
}: {
  drawer: Drawer;
  onSelect: (d: Drawer) => void;
  counts: Record<RailKey, number>;
}) {
  return (
    <nav className="rail" aria-label="Panels">
      {ITEMS.map(({ key, label }) => (
        <button
          key={key}
          className={`rail-btn${drawer === key ? ' active' : ''}`}
          onClick={() => onSelect(drawer === key ? null : key)}
          title={label}
        >
          <span className="rail-label">{label}</span>
          {counts[key] > 0 && <span className="rail-badge">{counts[key]}</span>}
        </button>
      ))}
    </nav>
  );
}
