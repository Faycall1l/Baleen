import type { GraphMode } from '../graph/types';
import type { GraphNode } from '../types';

interface Props {
  mode: GraphMode;
  onModeChange: (mode: GraphMode) => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  matches: GraphNode[];
  onChooseMatch: (node: GraphNode) => void;
  onFit: () => void;
}

export default function GraphToolbar({
  mode,
  onModeChange,
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
      <div className="graph-mode" role="tablist" aria-label="Graph view">
        <button
          className={mode === 'instances' ? 'active' : ''}
          onClick={() => onModeChange('instances')}
          role="tab"
          aria-selected={mode === 'instances'}
        >
          Instances
        </button>
        <button
          className={mode === 'schema' ? 'active' : ''}
          onClick={() => onModeChange('schema')}
          role="tab"
          aria-selected={mode === 'schema'}
        >
          Schema
        </button>
      </div>
      {mode === 'instances' ? (
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
      ) : (
        <div className="graph-hint">Click a type to inspect its instances</div>
      )}
      <button className="graph-fit-btn" onClick={onFit} title="Fit the whole graph in view">
        Fit
      </button>
    </div>
  );
}
