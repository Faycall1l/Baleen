import { useEffect, useState } from 'react';

import type { GraphNode } from '../types';

interface Props {
  hiddenTypes: Set<string>;
  typeCounts: Map<string, number>;
  onToggleType: (label: string) => void;
  onClearTypes: () => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  matches: GraphNode[];
  matchTotal: number;
  onChooseMatch: (node: GraphNode) => void;
  onFit: () => void;
  onResetView: () => void;
  scrubDaysAgo: number | null;
  scrubMaxDays: number;
  onScrubChange: (daysAgo: number | null) => void;
  showEvents: boolean;
  onToggleEvents: () => void;
}

export default function GraphToolbar({
  hiddenTypes,
  typeCounts,
  onToggleType,
  onClearTypes,
  query,
  onQueryChange,
  searchOpen,
  onSearchOpenChange,
  matches,
  matchTotal,
  onChooseMatch,
  onFit,
  onResetView,
  scrubDaysAgo,
  scrubMaxDays,
  onScrubChange,
  showEvents,
  onToggleEvents,
}: Props) {
  // Keyboard navigation through results: arrows move the highlight
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
              aria-label={`Show ${label} nodes (${typeCounts.get(label) ?? 0} hidden)`}
              title={`Show ${label} nodes`}
            >
              {label} · {typeCounts.get(label) ?? 0} ×
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
              <div className="graph-search-count" aria-live="polite">
                {matches.length} of {matchTotal} match{matchTotal === 1 ? '' : 'es'}
              </div>
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
                  <span className="graph-search-label">{m.label}</span> {m.title ?? m.id}
                </button>
              ))}
            </div>
          )}
        </div>
      <button className="graph-fit-btn" onClick={onFit} title="Fit the whole board in view">
        Fit
      </button>
      <button className="graph-fit-btn" onClick={onResetView} title="Fit, clear selection, and show all types">
        Reset
      </button>
      <button
        className={`graph-fit-btn${showEvents ? ' active' : ''}`}
        onClick={onToggleEvents}
        title={showEvents ? 'Hide event nodes (focused view)' : 'Show event nodes (full view)'}
        aria-pressed={showEvents}
      >
        {showEvents ? 'Events on' : 'Events off'}
      </button>
      <div className="time-scrub" title="Freeze the board on an as-of snapshot">
        <input
          type="range"
          className="time-scrub-slider"
          min={0}
          max={scrubMaxDays}
          step={1}
          value={scrubDaysAgo ?? 0}
          aria-label="Days back in time (0 is live)"
          onChange={(e) => {
            const days = Number(e.target.value);
            onScrubChange(days <= 0 ? null : days);
          }}
        />
        {scrubDaysAgo === null ? (
          <span className="time-scrub-label">LIVE</span>
        ) : (
          <button className="type-clear" onClick={() => onScrubChange(null)}>
            {scrubDaysAgo}d ago · back to live
          </button>
        )}
      </div>
    </div>
  );
}
