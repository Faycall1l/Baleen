import { useEffect, useState } from 'react';

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
  // Keyboard navigation through results (R27): arrows move the highlight
  // (wrapping), Enter picks it, Escape clears. Resets on new matches.
  const [activeIndex, setActiveIndex] = useState(0);
  useEffect(() => {
    setActiveIndex(0);
  }, [matches]);
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
              if (e.key === 'ArrowDown' && matches.length > 0) {
                e.preventDefault();
                setActiveIndex((i) => (i + 1) % matches.length);
              } else if (e.key === 'ArrowUp' && matches.length > 0) {
                e.preventDefault();
                setActiveIndex((i) => (i - 1 + matches.length) % matches.length);
              } else if (e.key === 'Enter' && matches.length > 0) {
                onChooseMatch(matches[Math.min(activeIndex, matches.length - 1)]);
              } else if (e.key === 'Escape') {
                onQueryChange('');
                onSearchOpenChange(false);
              }
            }}
            placeholder="Find a node…"
            aria-label="Find a node"
          />
          {searchOpen && matches.length > 0 && (
            <div className="graph-search-results" role="listbox" aria-label="Matching nodes">
              {matches.map((m, i) => (
                <button
                  key={m.id}
                  role="option"
                  aria-selected={i === activeIndex}
                  className={i === activeIndex ? 'active' : undefined}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setActiveIndex(i)}
                  onClick={() => onChooseMatch(m)}
                >
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
