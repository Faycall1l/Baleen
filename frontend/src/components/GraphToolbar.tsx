import type { GraphNode } from '../types';

interface Props {
  hiddenTypes: Set<string>;
  onToggleType: (label: string) => void;
  onClearTypes: () => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  matches: GraphNode[];
  onChooseMatch: (node: GraphNode) => void;
  onFit: () => void;
}

export default function GraphToolbar({
  hiddenTypes,
  onToggleType,
  onClearTypes,
  query,
  onQueryChange,
  searchOpen,
  onSearchOpenChange,
  matches,
  onChooseMatch,
  onFit,
}: Props) {
  return (
    <div className="graph-toolbar">
      {hiddenTypes.size > 0 && (
        <div className="type-filters" role="group" aria-label="Hidden node types">
          <span className="type-filters-label">Hidden:</span>
          {[...hiddenTypes].sort().map((label) => (
            <button
              key={label}
              className="type-pill"
              onClick={() => onToggleType(label)}
              aria-label={`Show ${label} nodes`}
              title={`Show ${label} nodes`}
            >
              {label} ×
            </button>
          ))}
          <button className="type-clear" onClick={onClearTypes}>
            Show all
          </button>
        </div>
      )}
      <div className="graph-search">
          <input
            value={query}
            onChange={(e) => {
              onQueryChange(e.target.value);
              onSearchOpenChange(true);
            }}
            onFocus={() => onSearchOpenChange(true)}
            onBlur={() => setTimeout(() => onSearchOpenChange(false), 150)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && matches.length > 0) onChooseMatch(matches[0]);
              if (e.key === 'Escape') {
                onQueryChange('');
                onSearchOpenChange(false);
              }
            }}
            placeholder="Find a node…"
            aria-label="Find a node"
          />
          {searchOpen && matches.length > 0 && (
            <div className="graph-search-results">
              {matches.map((m) => (
                <button key={m.id} onMouseDown={(e) => e.preventDefault()} onClick={() => onChooseMatch(m)}>
                  <span className="graph-search-label">{m.label}</span> {m.title}
                </button>
              ))}
            </div>
          )}
        </div>
      <button className="graph-fit-btn" onClick={onFit} title="Fit the whole graph in view">
        Fit
      </button>
    </div>
  );
}
